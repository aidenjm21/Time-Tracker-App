import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from collections import Counter
import io
import os
import re
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

# Set BST timezone (UTC+1)
BST = timezone(timedelta(hours=1))
UTC_PLUS_1 = BST  # Keep backward compatibility

@st.cache_resource
def init_database():
    """Initialise database connection and create tables"""
    try:
        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            st.error("Database URL not found. Please check your environment variables.")
            return None
        
        engine = create_engine(database_url)
        
        # Create table if it doesn't exist
        with engine.connect() as conn:
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS trello_time_tracking (
                    id SERIAL PRIMARY KEY,
                    card_name VARCHAR(500) NOT NULL,
                    user_name VARCHAR(255) NOT NULL,
                    list_name VARCHAR(255) NOT NULL,
                    time_spent_seconds INTEGER NOT NULL,
                    date_started DATE,
                    card_estimate_seconds INTEGER,
                    board_name VARCHAR(255),
                    labels TEXT,
                    archived BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(card_name, user_name, list_name, date_started, time_spent_seconds)
                )
            '''))
            # Add archived column to existing table if it doesn't exist
            conn.execute(text('''
                ALTER TABLE trello_time_tracking 
                ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE
            '''))
            
            # Add session_start_time column if it doesn't exist
            conn.execute(text('''
                ALTER TABLE trello_time_tracking 
                ADD COLUMN IF NOT EXISTS session_start_time TIMESTAMP
            '''))
            
            # Add tag column if it doesn't exist
            conn.execute(text('''
                ALTER TABLE trello_time_tracking 
                ADD COLUMN IF NOT EXISTS tag VARCHAR(255)
            '''))
            
            # Create active timers table for persistent timer storage
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS active_timers (
                    id SERIAL PRIMARY KEY,
                    timer_key VARCHAR(500) NOT NULL UNIQUE,
                    card_name VARCHAR(255) NOT NULL,
                    user_name VARCHAR(100),
                    list_name VARCHAR(100) NOT NULL,
                    board_name VARCHAR(100),
                    start_time TIMESTAMPTZ NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            '''))
            
            # Migrate existing TIMESTAMP columns to TIMESTAMPTZ if needed
            try:
                conn.execute(text('''
                    ALTER TABLE active_timers 
                    ALTER COLUMN start_time TYPE TIMESTAMPTZ USING start_time AT TIME ZONE 'Europe/London'
                '''))
                conn.execute(text('''
                    ALTER TABLE active_timers 
                    ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'Europe/London'
                '''))
            except Exception:
                # Columns might already be TIMESTAMPTZ, ignore the error
                pass
            conn.commit()
        
        return engine
    except Exception as e:
        st.error(f"Database initialisation failed: {str(e)}")
        return None



@st.cache_data(ttl=300)  # Cache for 5 minutes
def get_users_from_database(_engine):
    """Get list of unique users from database"""
    try:
        with _engine.connect() as conn:
            result = conn.execute(text('SELECT DISTINCT COALESCE(user_name, \'Not set\') FROM trello_time_tracking ORDER BY COALESCE(user_name, \'Not set\')'))
            return [row[0] for row in result]
    except Exception as e:
        st.error(f"Error fetching users: {str(e)}")
        return []

def get_tags_from_database(_engine):
    """Get list of unique tags from database"""
    try:
        with _engine.connect() as conn:
            result = conn.execute(text("SELECT DISTINCT tag FROM trello_time_tracking WHERE tag IS NOT NULL AND tag != '' ORDER BY tag"))
            tags = [row[0] for row in result]
            return tags
    except Exception as e:
        st.error(f"Error fetching tags: {str(e)}")
        return []

def get_books_from_database(_engine):
    """Get list of unique book names from database"""
    try:
        with _engine.connect() as conn:
            result = conn.execute(text("SELECT DISTINCT card_name FROM trello_time_tracking WHERE card_name IS NOT NULL ORDER BY card_name"))
            books = [row[0] for row in result]
            return books
    except Exception as e:
        st.error(f"Error fetching books: {str(e)}")
        return []

def get_boards_from_database(_engine):
    """Get list of unique board names from database"""
    try:
        with _engine.connect() as conn:
            result = conn.execute(text("SELECT DISTINCT board_name FROM trello_time_tracking WHERE board_name IS NOT NULL AND board_name != '' ORDER BY board_name"))
            boards = [row[0] for row in result]
            return boards
    except Exception as e:
        st.error(f"Error fetching boards: {str(e)}")
        return []


def load_active_timers(engine):
    """Load active timers from database and restore session state"""
    try:
        with engine.connect() as conn:
            result = conn.execute(text('''
                SELECT timer_key, card_name, user_name, list_name, board_name, start_time
                FROM active_timers
                ORDER BY start_time DESC
            '''))
            
            active_timers = []
            for row in result:
                timer_key = row[0]
                card_name = row[1]
                user_name = row[2]
                list_name = row[3]
                board_name = row[4]
                start_time = row[5]
                
                # Restore timer state in session
                if 'timers' not in st.session_state:
                    st.session_state.timers = {}
                if 'timer_start_times' not in st.session_state:
                    st.session_state.timer_start_times = {}
                
                st.session_state.timers[timer_key] = True
                # Ensure timezone-aware datetime for consistency
                if start_time.tzinfo is None:
                    start_time = start_time.replace(tzinfo=BST)
                elif start_time.tzinfo != BST:
                    # Convert to BST if it's in a different timezone
                    start_time = start_time.astimezone(BST)
                st.session_state.timer_start_times[timer_key] = start_time
                
                active_timers.append({
                    'timer_key': timer_key,
                    'card_name': card_name,
                    'user_name': user_name,
                    'list_name': list_name,
                    'board_name': board_name,
                    'start_time': start_time
                })
            
            return active_timers
    except Exception as e:
        st.error(f"Error loading active timers: {str(e)}")
        return []


def save_active_timer(engine, timer_key, card_name, user_name, list_name, board_name, start_time):
    """Save active timer to database"""
    try:
        with engine.connect() as conn:
            conn.execute(text('''
                INSERT INTO active_timers (timer_key, card_name, user_name, list_name, board_name, start_time)
                VALUES (:timer_key, :card_name, :user_name, :list_name, :board_name, :start_time)
                ON CONFLICT (timer_key) DO UPDATE SET
                    start_time = EXCLUDED.start_time,
                    created_at = CURRENT_TIMESTAMP
            '''), {
                'timer_key': timer_key,
                'card_name': card_name,
                'user_name': user_name,
                'list_name': list_name,
                'board_name': board_name,
                'start_time': start_time.astimezone(BST) if start_time.tzinfo else start_time.replace(tzinfo=BST)
            })
            conn.commit()
    except Exception as e:
        st.error(f"Error saving active timer: {str(e)}")


def remove_active_timer(engine, timer_key):
    """Remove active timer from database"""
    try:
        with engine.connect() as conn:
            conn.execute(text('''
                DELETE FROM active_timers WHERE timer_key = :timer_key
            '''), {'timer_key': timer_key})
            conn.commit()
    except Exception as e:
        st.error(f"Error removing active timer: {str(e)}")


def update_task_completion(engine, card_name, user_name, list_name, completed):
    """Update task completion status"""
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE trello_time_tracking 
                SET completed = :completed
                WHERE card_name = :card_name 
                AND COALESCE(user_name, 'Not set') = :user_name 
                AND list_name = :list_name
            """), {
                'completed': completed,
                'card_name': card_name,
                'user_name': user_name,
                'list_name': list_name
            })
            conn.commit()
    except Exception as e:
        st.error(f"Error updating task completion: {str(e)}")


def get_task_completion(engine, card_name, user_name, list_name):
    """Get task completion status"""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT completed FROM trello_time_tracking 
                WHERE card_name = :card_name 
                AND COALESCE(user_name, 'Not set') = :user_name 
                AND list_name = :list_name
                LIMIT 1
            """), {
                'card_name': card_name,
                'user_name': user_name,
                'list_name': list_name
            })
            row = result.fetchone()
            return row[0] if row else False
    except Exception as e:
        st.error(f"Error getting task completion: {str(e)}")
        return False


def check_all_tasks_completed(engine, card_name):
    """Check if all tasks for a book are completed"""
    try:
        with engine.connect() as conn:
            # Get all tasks for this book
            result = conn.execute(text("""
                SELECT DISTINCT list_name, COALESCE(user_name, 'Not set') as user_name, 
                       COALESCE(completed, false) as completed
                FROM trello_time_tracking 
                WHERE card_name = :card_name 
                AND archived = FALSE
            """), {
                'card_name': card_name
            })
            
            tasks = result.fetchall()
            if not tasks:
                return False
            
            # Check if all tasks are completed
            for task in tasks:
                if not task[2]:  # completed column
                    return False
            
            return True
    except Exception as e:
        st.error(f"Error checking book completion: {str(e)}")
        return False


def delete_task_stage(engine, card_name, user_name, list_name):
    """Delete a specific task stage from the database"""
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                DELETE FROM trello_time_tracking 
                WHERE card_name = :card_name 
                AND COALESCE(user_name, 'Not set') = :user_name 
                AND list_name = :list_name
            """), {
                'card_name': card_name,
                'user_name': user_name,
                'list_name': list_name
            })
            conn.commit()
            return True
    except Exception as e:
        st.error(f"Error deleting task stage: {str(e)}")
        return False


def create_book_record(engine, card_name, board_name=None, tag=None):
    """Create a book record in the books table"""
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO books (card_name, board_name, tag)
                VALUES (:card_name, :board_name, :tag)
                ON CONFLICT (card_name) DO UPDATE SET
                    board_name = EXCLUDED.board_name,
                    tag = EXCLUDED.tag
            """), {
                'card_name': card_name,
                'board_name': board_name,
                'tag': tag
            })
            conn.commit()
            return True
    except Exception as e:
        st.error(f"Error creating book record: {str(e)}")
        return False


def get_all_books(engine):
    """Get all books from the books table, including those without tasks"""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT DISTINCT card_name, board_name, tag
                FROM books
                WHERE archived = FALSE
                UNION
                SELECT DISTINCT card_name, board_name, tag
                FROM trello_time_tracking
                WHERE archived = FALSE
                ORDER BY card_name
            """))
            return result.fetchall()
    except Exception as e:
        st.error(f"Error fetching books: {str(e)}")
        return []


def get_available_stages_for_book(engine, card_name):
    """Get stages not yet associated with a book"""
    all_stages = [
        "Editorial R&D", "Editorial Writing", "1st Edit", "2nd Edit",
        "Design R&D", "In Design", "1st Proof", "2nd Proof", 
        "Editorial Sign Off", "Design Sign Off"
    ]
    
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT DISTINCT list_name
                FROM trello_time_tracking
                WHERE card_name = :card_name AND archived = FALSE
            """), {'card_name': card_name})
            
            existing_stages = [row[0] for row in result.fetchall()]
            available_stages = [stage for stage in all_stages if stage not in existing_stages]
            return available_stages
    except Exception as e:
        st.error(f"Error getting available stages: {str(e)}")
        return []


def add_stage_to_book(engine, card_name, stage_name, board_name=None, tag=None):
    """Add a new stage to a book"""
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO trello_time_tracking 
                (card_name, user_name, list_name, time_spent_seconds, card_estimate_seconds, board_name, created_at, tag)
                VALUES (:card_name, :user_name, :list_name, :time_spent_seconds, :card_estimate_seconds, :board_name, :created_at, :tag)
            """), {
                'card_name': card_name,
                'user_name': None,  # Unassigned initially
                'list_name': stage_name,
                'time_spent_seconds': 0,
                'card_estimate_seconds': 3600,  # Default 1 hour estimate
                'board_name': board_name,
                'created_at': datetime.now(BST),
                'tag': tag
            })
            conn.commit()
            return True
    except Exception as e:
        st.error(f"Error adding stage: {str(e)}")
        return False


def get_filtered_tasks_from_database(_engine, user_name=None, book_name=None, board_name=None, tag_name=None, start_date=None, end_date=None):
    """Get filtered tasks from database with multiple filter options"""
    try:
        query = '''
            WITH task_summary AS (
                SELECT card_name, list_name, COALESCE(user_name, 'Not set') as user_name, board_name, tag,
                       SUM(time_spent_seconds) as total_time,
                       MAX(card_estimate_seconds) as estimated_seconds,
                       MIN(CASE WHEN session_start_time IS NOT NULL THEN session_start_time END) as first_session
                FROM trello_time_tracking 
                WHERE 1=1
        '''
        params = {}
        
        # Add filters based on provided parameters
        if user_name and user_name != "All Users":
            query += ' AND COALESCE(user_name, \'Not set\') = :user_name'
            params['user_name'] = user_name
            
        if book_name and book_name != "All Books":
            query += ' AND card_name = :book_name'
            params['book_name'] = book_name
            
        if board_name and board_name != "All Boards":
            query += ' AND board_name = :board_name'
            params['board_name'] = board_name
            
        if tag_name and tag_name != "All Tags":
            query += ' AND tag = :tag_name'
            params['tag_name'] = tag_name
        
        query += '''
                GROUP BY card_name, list_name, COALESCE(user_name, 'Not set'), board_name, tag
            )
            SELECT card_name, list_name, user_name, board_name, tag, first_session, total_time, estimated_seconds
            FROM task_summary
        '''
        
        # Add date filtering to the main query if needed
        if start_date or end_date:
            date_conditions = []
            if start_date:
                date_conditions.append('first_session >= :start_date')
                params['start_date'] = start_date
            if end_date:
                date_conditions.append('first_session <= :end_date')
                params['end_date'] = end_date
            
            if date_conditions:
                query += ' WHERE ' + ' AND '.join(date_conditions)
        
        query += ' ORDER BY first_session DESC, card_name, list_name'
        
        with _engine.connect() as conn:
            result = conn.execute(text(query), params)
            data = []
            for row in result:
                card_name = row[0]
                list_name = row[1]
                user_name = row[2]
                board_name = row[3]
                tag = row[4]
                first_session = row[5]
                total_time = row[6]
                estimated_time = row[7] if row[7] else 0
                
                if first_session:
                    # Format as DD/MM/YYYY HH:MM
                    date_time_str = first_session.strftime('%d/%m/%Y %H:%M')
                else:
                    date_time_str = 'Manual Entry'
                    
                data.append({
                    'Book Title': card_name,
                    'Stage': list_name,
                    'User': user_name,
                    'Board': board_name,
                    'Tag': tag if tag else 'No Tag',
                    'Session Started': date_time_str,
                    'Time Allocation': format_seconds_to_time(estimated_time) if estimated_time > 0 else 'Not Set',
                    'Time Spent': format_seconds_to_time(total_time)
                })
            return pd.DataFrame(data)
    except Exception as e:
        st.error(f"Error fetching user tasks: {str(e)}")
        return pd.DataFrame()

def format_seconds_to_time(seconds):
    """Convert seconds to hh:mm:ss format"""
    if pd.isna(seconds) or seconds == 0:
        return "00:00:00"
    
    # Convert to integer to handle any float values
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

def calculate_completion_status(time_spent_seconds, estimated_seconds):
    """Calculate completion status based on time spent vs estimated time"""
    if pd.isna(estimated_seconds) or estimated_seconds == 0:
        return "No estimate"
    
    completion_ratio = time_spent_seconds / estimated_seconds
    
    if completion_ratio <= 1.0:
        percentage = int(completion_ratio * 100)
        return f"{percentage}% Complete"
    else:
        over_percentage = int((completion_ratio - 1.0) * 100)
        return f"{over_percentage}% over allocation"

def process_book_summary(df):
    """Generate Book Summary Table"""
    try:
        # Group by book title (Card name)
        book_groups = df.groupby('Card name')
        
        book_summary_data = []
        
        for book_title, group in book_groups:
            # Calculate total time spent
            total_time_spent = group['Time spent (s)'].sum()
            
            # Find main user (most frequent contributor by time spent)
            user_time = group.groupby('User')['Time spent (s)'].sum()
            main_user = user_time.idxmax() if not user_time.empty else "Unknown"
            
            # Get estimated time (assuming it's the same for all rows of the same book)
            estimated_time = group['Card estimate(s)'].iloc[0] if 'Card estimate(s)' in group.columns else 0
            if pd.isna(estimated_time):
                estimated_time = 0
            
            # Get board name (assuming it's the same for all rows of the same book)
            board_name = group['Board'].iloc[0] if 'Board' in group.columns else "Unknown"
            if pd.isna(board_name):
                board_name = "Unknown"
            
            # Calculate completion status
            completion = calculate_completion_status(total_time_spent, estimated_time)
            
            book_summary_data.append({
                'Book Title': book_title,
                'Board': board_name,
                'Main User': main_user,
                'Time Spent': format_seconds_to_time(total_time_spent),
                'Estimated Time': format_seconds_to_time(estimated_time),
                'Completion': completion
            })
        
        return pd.DataFrame(book_summary_data)
    
    except Exception as e:
        st.error(f"Error processing book summary: {str(e)}")
        return pd.DataFrame()

def get_most_recent_activity(df, card_name):
    """Get the most recent list/stage worked on for a specific card"""
    try:
        card_data = df[df['Card name'] == card_name]
        
        if card_data.empty:
            return "Unknown"
        
        # If Date started (f) exists, use it to find most recent
        if 'Date started (f)' in df.columns and not card_data['Date started (f)'].isna().all():
            # Convert dates and find the most recent entry
            card_data_with_dates = card_data.dropna(subset=['Date started (f)'])
            if not card_data_with_dates.empty:
                card_data_with_dates = card_data_with_dates.copy()
                card_data_with_dates['parsed_date'] = pd.to_datetime(card_data_with_dates['Date started (f)'], format='%m/%d/%Y', errors='coerce')
                card_data_with_dates = card_data_with_dates.dropna(subset=['parsed_date'])
                if not card_data_with_dates.empty:
                    most_recent = card_data_with_dates.loc[card_data_with_dates['parsed_date'].idxmax()]
                    return most_recent['List']
        
        # Fallback: return the last entry (by order in CSV)
        return card_data.iloc[-1]['List']
    except Exception as e:
        return "Unknown"

def create_progress_bar_html(completion_percentage):
    """Create HTML progress bar for completion status"""
    if completion_percentage <= 100:
        # Normal progress (green)
        width = min(completion_percentage, 100)
        color = "#28a745"  # Green
        return f"""
        <div style="margin-bottom: 5px;">
            <div style="background-color: #f0f0f0; border-radius: 10px; padding: 2px; width: 200px; height: 20px;">
                <div style="background-color: {color}; width: {width}%; height: 16px; border-radius: 8px;"></div>
            </div>
            <div style="font-size: 12px; font-weight: bold; color: {color}; text-align: center;">
                {completion_percentage:.1f}% complete
            </div>
        </div>
        """
    else:
        # Over allocation (red with overflow)
        over_percentage = completion_percentage - 100
        return f"""
        <div style="margin-bottom: 5px;">
            <div style="background-color: #f0f0f0; border-radius: 10px; padding: 2px; width: 200px; height: 20px;">
                <div style="background-color: #dc3545; width: 100%; height: 16px; border-radius: 8px;"></div>
            </div>
            <div style="font-size: 12px; font-weight: bold; color: #dc3545; text-align: center;">
                {over_percentage:.1f}% over allocation
            </div>
        </div>
        """

def process_book_completion(df, search_filter=None):
    """Generate Book Completion Table with visual progress"""
    try:
        # Apply search filter if provided
        if search_filter:
            # Escape special regex characters to handle punctuation properly
            escaped_filter = re.escape(search_filter)
            df = df[df['Card name'].str.contains(escaped_filter, case=False, na=False)]
            
        if df.empty:
            return pd.DataFrame()
        
        # Group by book title (Card name)
        book_groups = df.groupby('Card name')
        
        book_completion_data = []
        
        for book_title, group in book_groups:
            # Calculate total time spent
            total_time_spent = group['Time spent (s)'].sum()
            
            # Get estimated time (assuming it's the same for all rows of the same book)
            estimated_time = 0
            if 'Card estimate(s)' in group.columns and len(group) > 0:
                est_val = group['Card estimate(s)'].iloc[0]
                if not pd.isna(est_val):
                    estimated_time = est_val
            
            # Get most recent activity
            most_recent_list = get_most_recent_activity(df, book_title)
            
            # Calculate completion status
            completion = calculate_completion_status(total_time_spent, estimated_time)
            
            # Create visual progress element
            if estimated_time > 0:
                completion_percentage = (total_time_spent / estimated_time) * 100
                progress_bar_html = create_progress_bar_html(completion_percentage)
            else:
                progress_bar_html = '<div style="font-style: italic; color: #666;">No estimate</div>'
            
            visual_progress = f"""
            <div style="padding: 10px; border: 1px solid #ddd; border-radius: 8px; margin: 2px 0; background-color: #fafafa;">
                <div style="font-weight: bold; font-size: 14px; margin-bottom: 5px; color: #000;">{book_title}</div>
                <div style="font-size: 12px; color: #666; margin-bottom: 8px;">Current stage: {most_recent_list}</div>
                <div>{progress_bar_html}</div>
            </div>
            """
            
            book_completion_data.append({
                'Book Title': book_title,
                'Visual Progress': visual_progress,
            })
        
        return pd.DataFrame(book_completion_data)
    
    except Exception as e:
        st.error(f"Error processing book completion: {str(e)}")
        return pd.DataFrame()

def convert_date_format(date_str):
    """Convert date from mm/dd/yyyy format to dd/mm/yyyy format"""
    try:
        if pd.isna(date_str) or date_str == 'N/A':
            return 'N/A'
        
        # Parse the date string - handle both with and without time
        if ' ' in str(date_str):
            # Has time component
            date_part, time_part = str(date_str).split(' ', 1)
            date_obj = datetime.strptime(date_part, '%m/%d/%Y')
            return f"{date_obj.strftime('%d/%m/%Y')} {time_part}"
        else:
            # Date only
            date_obj = datetime.strptime(str(date_str), '%m/%d/%Y')
            return date_obj.strftime('%d/%m/%Y')
    except:
        return str(date_str)  # Return original if conversion fails

def process_user_task_breakdown(df):
    """Generate User Task Breakdown Table with aggregated time"""
    try:
        # Check if Date started column exists in the CSV
        has_date = 'Date started (f)' in df.columns
        
        if has_date:
            # Convert date format from mm/dd/yyyy to datetime for proper sorting
            df_copy = df.copy()
            
            # Try multiple date formats to handle different possible formats
            df_copy['Date_parsed'] = pd.to_datetime(df_copy['Date started (f)'], errors='coerce')
            
            # If initial parsing failed, try specific formats
            if df_copy['Date_parsed'].isna().all():
                # Try mm/dd/yyyy format without time
                df_copy['Date_parsed'] = pd.to_datetime(df_copy['Date started (f)'], format='%m/%d/%Y', errors='coerce')
            
            # Group by User, Book Title, and List to aggregate multiple sessions
            # For each group, sum the time and take the earliest date
            agg_funcs = {
                'Time spent (s)': 'sum',
                'Date_parsed': 'min',  # Get earliest date
                'Date started (f)': 'first'  # Keep original format for fallback
            }
            
            aggregated = df_copy.groupby(['User', 'Card name', 'List']).agg(agg_funcs).reset_index()
            
            # Convert the earliest date back to dd/mm/yyyy format for display (date only, no time)
            def format_date_display(date_val):
                if pd.notna(date_val):
                    return date_val.strftime('%d/%m/%Y')
                else:
                    return 'N/A'
            
            aggregated['Date_display'] = aggregated['Date_parsed'].apply(format_date_display)
            
            # Rename columns for clarity
            aggregated = aggregated[['User', 'Card name', 'List', 'Date_display', 'Time spent (s)']]
            aggregated.columns = ['User', 'Book Title', 'List', 'Date', 'Time Spent (s)']
            
        else:
            # Group by User, Book Title (Card name), and List (stage/task)
            # Aggregate time spent for duplicate combinations
            aggregated = df.groupby(['User', 'Card name', 'List'])['Time spent (s)'].sum().reset_index()
            
            # Rename columns for clarity
            aggregated.columns = ['User', 'Book Title', 'List', 'Time Spent (s)']
            
            # Add empty Date column if not present
            aggregated['Date'] = 'N/A'
        
        # Format time spent
        aggregated['Time Spent'] = aggregated['Time Spent (s)'].apply(format_seconds_to_time)
        
        # Drop the seconds column as we now have formatted time
        aggregated = aggregated.drop('Time Spent (s)', axis=1)
        
        # Reorder columns to put Date after List
        aggregated = aggregated[['User', 'Book Title', 'List', 'Date', 'Time Spent']]
        
        # Sort by User → Book Title → List
        aggregated = aggregated.sort_values(['User', 'Book Title', 'List'])
        
        return aggregated.reset_index(drop=True)
    
    except Exception as e:
        st.error(f"Error processing user task breakdown: {str(e)}")
        return pd.DataFrame()



def main():
    # Add custom CSS to reduce padding and margins
    st.markdown("""
    <style>
    .main .block-container {
        padding-top: 1rem;
        padding-bottom: 1rem;
        padding-left: 1rem;
        padding-right: 1rem;
    }
    .stExpander > div:first-child {
        padding: 0.5rem 0;
    }
    .element-container {
        margin-bottom: 0.5rem;
    }
    div[data-testid="column"] {
        padding: 0 0.5rem;
    }
    </style>
    """, unsafe_allow_html=True)
    
    st.title("Book Production Time Tracking")
    st.markdown("Track time spent on different stages of book production with detailed stage-specific analysis.")
    
    # Initialise database
    engine = init_database()
    if not engine:
        st.error("Could not connect to database. Please check your configuration.")
        return
    
    # Initialize session state for active tab
    if 'active_tab' not in st.session_state:
        st.session_state.active_tab = 0
    
    # Initialize timer session state
    if 'timers' not in st.session_state:
        st.session_state.timers = {}
    if 'timer_start_times' not in st.session_state:
        st.session_state.timer_start_times = {}
    
    # Load and restore active timers from database
    if 'timers_loaded' not in st.session_state:
        active_timers = load_active_timers(engine)
        st.session_state.timers_loaded = True
        if active_timers:
            st.info(f"Restored {len(active_timers)} active timer(s) from previous session.")
    
    # Create tabs for different views
    tab_names = ["Book Progress", "Add Book", "Archive", "Reporting"]
    selected_tab = st.selectbox("Select Tab:", tab_names, index=st.session_state.active_tab, key="tab_selector")
    
    # Update active tab when changed - force immediate update
    current_index = tab_names.index(selected_tab)
    if current_index != st.session_state.active_tab:
        st.session_state.active_tab = current_index
        st.rerun()
    
    # Create individual tab sections based on selection
    if selected_tab == "Add Book":
        # Manual Data Entry Form
        st.header("Manual Data Entry")
        st.markdown("Add individual time tracking entries for detailed stage-specific analysis.")
        
        # Check if form should be cleared
        clear_form = st.session_state.get('clear_form', False)
        if clear_form:
            # Define all form field keys that need to be cleared
            form_keys_to_clear = [
                "manual_card_name", "manual_board_name", "manual_tag_select", "manual_add_new_tag", "manual_new_tag",
                # Time tracking field keys
                "user_editorial_r&d", "time_editorial_r&d",
                "user_editorial_writing", "time_editorial_writing", 
                "user_1st_edit", "time_1st_edit",
                "user_2nd_edit", "time_2nd_edit",
                "user_design_r&d", "time_design_r&d",
                "user_in_design", "time_in_design",
                "user_1st_proof", "time_1st_proof",
                "user_2nd_proof", "time_2nd_proof",
                "user_editorial_sign_off", "time_editorial_sign_off",
                "user_design_sign_off", "time_design_sign_off"
            ]
            
            # Clear all form field keys from session state
            for key in form_keys_to_clear:
                if key in st.session_state:
                    del st.session_state[key]
            
            # Clear the flag
            del st.session_state['clear_form']
        
        # General fields
        col1, col2 = st.columns(2)
        with col1:
            card_name = st.text_input("Card Name", placeholder="Enter book title", key="manual_card_name", value="" if clear_form else None)
        with col2:
            board_name = st.text_input("Board", placeholder="Enter board name", key="manual_board_name", value="" if clear_form else None)
            
        # Tag field
        existing_tags = get_tags_from_database(engine)
        tag_options = [""] + existing_tags  # Empty option for no tag
        
        # Create tag input - allow selecting existing or adding new
        col1, col2 = st.columns([3, 1])
        with col1:
            selected_tag = st.selectbox(
                "Tag (optional)",
                tag_options,
                key="manual_tag_select",
                help="Select an existing tag or choose 'Add New' to create a new tag",
                index=0 if clear_form else None
            )
        with col2:
            add_new_tag = st.checkbox("Add New", key="manual_add_new_tag", value=False if clear_form else None)
        
        # If user wants to add new tag, show text input
        if add_new_tag:
            new_tag = st.text_input("New Tag", placeholder="Enter new tag name", key="manual_new_tag", value="" if clear_form else None)
            final_tag = new_tag.strip() if new_tag else None
        else:
            final_tag = selected_tag if selected_tag else None
            
        st.subheader("Task Assignment & Estimates")
        st.markdown("*Assign users to stages and set time estimates. All tasks start with 0 actual time - use the Book Completion tab to track actual work time.*")
        
        # Define user groups for different types of work (alphabetically ordered)
        editorial_users = ["Not set", "Bethany Latham", "Charis Mather", "Noah Leatherland", "Rebecca Phillips-Bartlett"]
        design_users = ["Not set", "Amelia Harris", "Amy Li", "Drue Rintoul", "Jasmine Pointer", "Ker Ker Lee", "Rob Delph"]
        
        # Time tracking fields with specific user groups
        time_fields = [
            ("Editorial R&D", "Editorial R&D", editorial_users),
            ("Editorial Writing", "Editorial Writing", editorial_users),
            ("1st Edit", "1st Edit", editorial_users),
            ("2nd Edit", "2nd Edit", editorial_users),
            ("Design R&D", "Design R&D", design_users),
            ("In Design", "In Design", design_users),
            ("1st Proof", "1st Proof", editorial_users),
            ("2nd Proof", "2nd Proof", editorial_users),
            ("Editorial Sign Off", "Editorial Sign Off", editorial_users),
            ("Design Sign Off", "Design Sign Off", design_users)
        ]
        
        # Calculate and display time estimations in real-time
        editorial_total = 0.0
        design_total = 0.0
        time_entries = {}
        
        editorial_fields = ["Editorial R&D", "Editorial Writing", "1st Edit", "2nd Edit", "1st Proof", "2nd Proof", "Editorial Sign Off"]
        design_fields = ["Design R&D", "In Design", "Design Sign Off"]
        
        for field_label, list_name, user_options in time_fields:
            st.markdown(f"**{field_label} (hours)**")
            col1, col2 = st.columns([2, 1])
            
            with col1:
                selected_user = st.selectbox(
                    f"User for {field_label}",
                    user_options,
                    key=f"user_{list_name.replace(' ', '_').lower()}",
                    label_visibility="collapsed"
                )
            
            with col2:
                time_value = st.number_input(
                    f"Time for {field_label}",
                    min_value=0.0,
                    step=0.1,
                    format="%.1f",
                    key=f"time_{list_name.replace(' ', '_').lower()}",
                    label_visibility="collapsed"
                )
            
            # Handle user selection and calculate totals
            # Allow time entries with or without user assignment
            if time_value and time_value > 0:
                final_user = selected_user if selected_user != "Not set" else None
                
                # Store the entry (user can be None for unassigned tasks)
                time_entries[list_name] = {
                    'user': final_user,
                    'time_hours': time_value
                }
                
                # Add to category totals
                if list_name in editorial_fields:
                    editorial_total += time_value
                elif list_name in design_fields:
                    design_total += time_value
        
        total_estimation = editorial_total + design_total
        
        # Display real-time calculations
        st.markdown("---")
        st.markdown("**Time Estimations:**")
        st.write(f"Editorial Time Estimation: {editorial_total:.1f} hours")
        st.write(f"Design Time Estimation: {design_total:.1f} hours")
        st.write(f"**Total Time Estimation: {total_estimation:.1f} hours**")
        st.markdown("---")
        

        
        st.markdown("---")
        
        # Submit button outside of form
        if st.button("Add Entry", type="primary", key="manual_submit"):
            if not card_name:
                st.error("Please fill in Card Name field")
            else:
                try:
                    entries_added = 0
                    current_time = datetime.now(BST)
                    
                    # Always create a book record first
                    create_book_record(engine, card_name, board_name, final_tag)
                    
                    with engine.connect() as conn:
                        # Add estimate entries (task assignments with 0 time spent) if any exist
                        for list_name, entry_data in time_entries.items():
                            # Create task entry with 0 time spent - users will use timer to track actual time
                            # The time_hours value from the form is just for estimation display, not actual time spent
                            
                            # Convert hours to seconds for estimate
                            estimate_seconds = int(entry_data['time_hours'] * 3600)
                            
                            # Insert into database with 0 time spent but store the estimate
                            conn.execute(text('''
                                INSERT INTO trello_time_tracking 
                                (card_name, user_name, list_name, time_spent_seconds, card_estimate_seconds, board_name, created_at, session_start_time, tag)
                                VALUES (:card_name, :user_name, :list_name, :time_spent_seconds, :card_estimate_seconds, :board_name, :created_at, :session_start_time, :tag)
                            '''), {
                                'card_name': card_name,
                                'user_name': entry_data['user'],
                                'list_name': list_name,
                                'time_spent_seconds': 0,  # Start with 0 time spent
                                'card_estimate_seconds': estimate_seconds,  # Store the estimate
                                'board_name': board_name if board_name else None,
                                'created_at': current_time,
                                'session_start_time': None,  # No active session for manual entries
                                'tag': final_tag
                            })
                            entries_added += 1
                        
                        conn.commit()
                    
                    # Keep user on the Add Book tab
                    st.session_state.active_tab = 1  # Add Book tab
                    
                    if entries_added > 0:
                        # Store success message in session state for permanent display
                        st.session_state.book_created_message = f"Book '{card_name}' created successfully with {entries_added} time estimates!"
                    else:
                        # Book created without tasks
                        st.session_state.book_created_message = f"Book '{card_name}' created successfully! You can add tasks later from the Book Progress tab."
                    
                    # Set flag to clear form on next render instead of modifying session state directly
                    st.session_state.clear_form = True
                    
                    st.rerun()
                        
                except Exception as e:
                    st.error(f"Error adding manual entry: {str(e)}")
        
        # Show permanent success message if book was created (below the button)
        if 'book_created_message' in st.session_state:
            st.success(st.session_state.book_created_message)
    
    elif selected_tab == "Book Progress":
        st.header("Book Completion Progress")
        st.markdown("Visual progress tracking for all books with individual task timers.")
        
        # Display active timers at the top
        active_timer_count = sum(1 for running in st.session_state.timers.values() if running)
        if active_timer_count > 0:
            st.info(f"⏱️ {active_timer_count} timer(s) currently running - these will persist even if you refresh the page or close the tab")
            
            # Show details of active timers
            with st.expander("View Active Timers", expanded=False):
                for task_key, is_running in st.session_state.timers.items():
                    if is_running and task_key in st.session_state.timer_start_times:
                        # Extract book, stage, and user from task_key
                        parts = task_key.split('_')
                        if len(parts) >= 3:
                            book_title = '_'.join(parts[:-2])
                            stage_name = parts[-2]
                            user_name = parts[-1]
                            
                            start_time = st.session_state.timer_start_times[task_key]
                            # Ensure timezone-aware datetime for calculations
                            if start_time.tzinfo is None:
                                start_time = start_time.replace(tzinfo=BST)
                            
                            # Calculate current elapsed time using consistent UTC-based approach
                            current_time = datetime.utcnow().replace(tzinfo=timezone.utc).astimezone(BST)
                            elapsed = current_time - start_time
                            elapsed_str = str(elapsed).split('.')[0]  # Remove microseconds
                            
                            st.write(f"📚 **{book_title}** - {stage_name} ({user_name}) - Running for {elapsed_str}")
        
        # Initialize session state for timers
        if 'timers' not in st.session_state:
            st.session_state.timers = {}
        if 'timer_start_times' not in st.session_state:
            st.session_state.timer_start_times = {}
        
        # Check if we have data from database
        try:
            with engine.connect() as conn:
                result = conn.execute(text("SELECT COUNT(*) FROM trello_time_tracking"))
                total_records = result.scalar()
                
            if total_records and total_records > 0:
                st.info(f"Showing completion progress for books from {total_records} database records.")
                
                # Get all books including those without tasks
                all_books = get_all_books(engine)
                
                # Get task data from database for book completion (exclude archived)
                df_from_db = pd.read_sql(
                    '''SELECT card_name as "Card name", 
                       COALESCE(user_name, 'Not set') as "User", 
                       list_name as "List", 
                       time_spent_seconds as "Time spent (s)", 
                       date_started as "Date started (f)", 
                       card_estimate_seconds as "Card estimate(s)", 
                       board_name as "Board", created_at, tag as "Tag"
                       FROM trello_time_tracking WHERE archived = FALSE ORDER BY created_at DESC''', 
                    engine
                )
                
                if not df_from_db.empty:
                    # Add search bar for book titles
                    search_query = st.text_input(
                        "Search books by title:",
                        placeholder="Enter book title to filter results...",
                        help="Search for specific books by typing part of the title",
                        key="completion_search"
                    )
                    
                    # Filter books based on search
                    filtered_df = df_from_db.copy()
                    if search_query:
                        mask = filtered_df['Card name'].str.contains(search_query, case=False, na=False)
                        filtered_df = filtered_df[mask]
                    
                    # Get unique books from both sources and sort alphabetically
                    books_with_tasks = set(filtered_df['Card name'].unique()) if not filtered_df.empty else set()
                    books_without_tasks = set(book[0] for book in all_books if book[0] not in books_with_tasks)
                    
                    # Filter books without tasks based on search query
                    if search_query:
                        books_without_tasks = {book for book in books_without_tasks if search_query.lower() in book.lower()}
                    
                    all_unique_books = sorted(books_with_tasks | books_without_tasks)
                    
                    if len(all_unique_books) > 0:
                        st.write(f"Found {len(all_unique_books)} books to display")
                        
                        # Initialize session state for expanded books
                        if 'expanded_books' not in st.session_state:
                            st.session_state.expanded_books = []
                        
                        # Display each book with enhanced visualization
                        for book_title in all_unique_books:
                            # Check if book has tasks
                            if not filtered_df.empty:
                                book_mask = filtered_df['Card name'] == book_title
                                book_data = filtered_df[book_mask].copy()
                            else:
                                book_data = pd.DataFrame()
                            
                            # If book has no tasks, create empty data structure
                            if book_data.empty:
                                # Get book info from all_books
                                book_info = next((book for book in all_books if book[0] == book_title), None)
                                if book_info:
                                    # Create minimal book data structure
                                    book_data = pd.DataFrame({
                                        'Card name': [book_title],
                                        'User': ['Not set'],
                                        'List': ['No tasks assigned'],
                                        'Time spent (s)': [0],
                                        'Date started (f)': [None],
                                        'Card estimate(s)': [0],
                                        'Board': [book_info[1] if book_info[1] else 'Not set'],
                                        'Tag': [book_info[2] if book_info[2] else None]
                                    })
                            
                            # Calculate overall progress using stage-based estimates
                            total_time_spent = book_data['Time spent (s)'].sum()
                            
                            # Calculate total estimated time from the database entries
                            # Sum up all estimates stored in the database for this book
                            estimated_time = 0
                            if 'Card estimate(s)' in book_data.columns:
                                book_estimates = book_data['Card estimate(s)'].fillna(0).sum()
                                if book_estimates > 0:
                                    estimated_time = book_estimates
                            
                            # If no estimates in database, use reasonable defaults per stage
                            if estimated_time == 0:
                                default_stage_estimates = {
                                    'Editorial R&D': 2 * 3600,        # 2 hours default
                                    'Editorial Writing': 8 * 3600,    # 8 hours default 
                                    '1st Edit': 4 * 3600,             # 4 hours default
                                    '2nd Edit': 2 * 3600,             # 2 hours default
                                    'Design R&D': 3 * 3600,           # 3 hours default
                                    'In Design': 6 * 3600,            # 6 hours default
                                    '1st Proof': 2 * 3600,            # 2 hours default
                                    '2nd Proof': 1.5 * 3600,          # 1.5 hours default
                                    'Editorial Sign Off': 0.5 * 3600, # 30 minutes default
                                    'Design Sign Off': 0.5 * 3600     # 30 minutes default
                                }
                                unique_stages = book_data['List'].unique()
                                estimated_time = sum(default_stage_estimates.get(stage, 3600) for stage in unique_stages)
                            
                            # Calculate completion percentage for display
                            if estimated_time > 0:
                                completion_percentage = (total_time_spent / estimated_time) * 100
                                progress_text = f"{format_seconds_to_time(total_time_spent)}/{format_seconds_to_time(estimated_time)} ({completion_percentage:.1f}%)"
                            else:
                                completion_percentage = 0
                                progress_text = f"Total: {format_seconds_to_time(total_time_spent)} (No estimate)"
                            
                            # Auto-expand if there are active timers for this book or if it was manually expanded
                            has_active_timer = any(
                                st.session_state.timers.get(f"{book_title}_{stage}_{user}", False)
                                for stage in ["Editorial R&D", "Editorial Writing", "1st Edit", "2nd Edit", "Design R&D", "In Design", "1st Proof", "2nd Proof", "Editorial Sign Off", "Design Sign Off"]
                                for user in book_data['User'].unique()
                            )
                            
                            # Initialize expanded state if not exists
                            expanded_key = f"expanded_{book_title}"
                            if expanded_key not in st.session_state:
                                st.session_state[expanded_key] = has_active_timer
                            
                            # Keep expanded if there are active timers or if user manually expanded
                            should_expand = has_active_timer or st.session_state.get(expanded_key, False)
                            
                            # Check if all tasks are completed
                            all_tasks_completed = check_all_tasks_completed(engine, book_title)
                            completion_emoji = "✅ " if all_tasks_completed else ""
                            
                            # Create book title with progress percentage
                            if estimated_time > 0:
                                if completion_percentage > 100:
                                    over_percentage = completion_percentage - 100
                                    book_title_with_progress = f"{completion_emoji}**{book_title}** ({over_percentage:.1f}% over estimate)"
                                else:
                                    book_title_with_progress = f"{completion_emoji}**{book_title}** ({completion_percentage:.1f}%)"
                            else:
                                book_title_with_progress = f"{completion_emoji}**{book_title}** (No estimate)"
                            
                            with st.expander(book_title_with_progress, expanded=should_expand):
                                # Show progress bar and completion info at the top
                                progress_bar_html = f"""
                                <div style="width: 50%; background-color: #f0f0f0; border-radius: 5px; height: 10px; margin: 8px 0;">
                                    <div style="width: {min(completion_percentage, 100):.1f}%; background-color: #007bff; height: 100%; border-radius: 5px;"></div>
                                </div>
                                """
                                st.markdown(progress_bar_html, unsafe_allow_html=True)
                                st.markdown(f'<div style="font-size: 14px; color: #666; margin-bottom: 10px;">{progress_text}</div>', unsafe_allow_html=True)
                                
                                # Display tag if available
                                book_tags = book_data['Tag'].dropna().unique()
                                if len(book_tags) > 0 and book_tags[0]:
                                    tag_display = book_tags[0]
                                    st.markdown(f'<div style="font-size: 14px; color: #888; margin-bottom: 10px;"><strong>Tag:</strong> {tag_display}</div>', unsafe_allow_html=True)
                                
                                st.markdown("---")
                                
                                # Define the order of stages to match the actual data entry form
                                stage_order = [
                                    'Editorial R&D', 'Editorial Writing', '1st Edit', '2nd Edit',
                                    'Design R&D', 'In Design', '1st Proof', '2nd Proof', 
                                    'Editorial Sign Off', 'Design Sign Off'
                                ]
                                
                                # Group by stage/list and aggregate by user
                                stages_grouped = book_data.groupby('List')
                                
                                # Display stages in accordion style (each stage as its own expander)
                                stage_counter = 0
                                for stage_name in stage_order:
                                    if stage_name in stages_grouped.groups:
                                        stage_data = stages_grouped.get_group(stage_name)
                                        
                                        # Check if this stage has any active timers
                                        stage_has_active_timer = any(
                                            st.session_state.timers.get(f"{book_title}_{stage_name}_{user}", False)
                                            for user in stage_data['User'].unique()
                                        )
                                        
                                        # Initialize stage expanded state
                                        stage_expanded_key = f"stage_expanded_{book_title}_{stage_name}"
                                        if stage_expanded_key not in st.session_state:
                                            st.session_state[stage_expanded_key] = stage_has_active_timer
                                        
                                        # Keep expanded if there are active timers
                                        should_expand_stage = stage_has_active_timer or st.session_state.get(stage_expanded_key, False)
                                        
                                        # Aggregate time by user for this stage
                                        user_aggregated = stage_data.groupby('User')['Time spent (s)'].sum().reset_index()
                                        
                                        # Create a summary for the expander title showing all users and their progress
                                        stage_summary_parts = []
                                        for idx, user_task in user_aggregated.iterrows():
                                            user_name = user_task['User']
                                            actual_time = user_task['Time spent (s)']
                                            
                                            # Get estimated time from the database for this specific user/stage combination
                                            user_stage_data = stage_data[stage_data['User'] == user_name]
                                            estimated_time_for_user = 3600  # Default 1 hour
                                            
                                            if not user_stage_data.empty and 'Card estimate(s)' in user_stage_data.columns:
                                                # Find the first record that has a non-null, non-zero estimate
                                                estimates = user_stage_data['Card estimate(s)'].dropna()
                                                non_zero_estimates = estimates[estimates > 0]
                                                if not non_zero_estimates.empty:
                                                    estimated_time_for_user = non_zero_estimates.iloc[0]
                                            
                                            # Check if task is completed
                                            is_completed = get_task_completion(engine, book_title, user_name, stage_name)
                                            completion_emoji = "✅ " if is_completed else ""
                                            
                                            # Format times for display
                                            actual_time_str = format_seconds_to_time(actual_time)
                                            estimated_time_str = format_seconds_to_time(estimated_time_for_user)
                                            user_display = user_name if user_name and user_name != "Not set" else "Unassigned"
                                            
                                            stage_summary_parts.append(f"{completion_emoji}{user_display} | {actual_time_str}/{estimated_time_str}")
                                        
                                        # Create expander title with stage name and user summaries
                                        if stage_summary_parts:
                                            expander_title = f"**{stage_name}** | " + " | ".join(stage_summary_parts)
                                        else:
                                            expander_title = stage_name
                                        
                                        with st.expander(expander_title, expanded=should_expand_stage):
                                            # Show one task per user for this stage
                                            for idx, user_task in user_aggregated.iterrows():
                                                user_name = user_task['User']
                                                actual_time = user_task['Time spent (s)']
                                                task_key = f"{book_title}_{stage_name}_{user_name}"
                                                
                                                # Get estimated time from the database for this specific user/stage combination
                                                user_stage_data = stage_data[stage_data['User'] == user_name]
                                                estimated_time_for_user = 3600  # Default 1 hour
                                                
                                                if not user_stage_data.empty and 'Card estimate(s)' in user_stage_data.columns:
                                                    # Find the first record that has a non-null, non-zero estimate
                                                    estimates = user_stage_data['Card estimate(s)'].dropna()
                                                    non_zero_estimates = estimates[estimates > 0]
                                                    if not non_zero_estimates.empty:
                                                        estimated_time_for_user = non_zero_estimates.iloc[0]
                                                
                                                # Create columns for task info and timer
                                                col1, col2, col3 = st.columns([4, 1, 3])
                                                
                                                with col1:
                                                    # User assignment dropdown
                                                    current_user = user_name if user_name else "Not set"
                                                    
                                                    # Determine user options based on stage type
                                                    if stage_name in ["Editorial R&D", "Editorial Writing", "1st Edit", "2nd Edit", "1st Proof", "2nd Proof", "Editorial Sign Off"]:
                                                        user_options = ["Not set", "Bethany Latham", "Charis Mather", "Noah Leatherland", "Rebecca Phillips-Bartlett"]
                                                    else:  # Design stages
                                                        user_options = ["Not set", "Amelia Harris", "Amy Li", "Drue Rintoul", "Jasmine Pointer", "Ker Ker Lee", "Rob Delph"]
                                                    
                                                    # Find current user index
                                                    try:
                                                        current_index = user_options.index(current_user)
                                                    except ValueError:
                                                        current_index = 0  # Default to "Not set"
                                                    
                                                    new_user = st.selectbox(
                                                        f"User for {stage_name}:",
                                                        user_options,
                                                        index=current_index,
                                                        key=f"reassign_{book_title}_{stage_name}_{user_name}"
                                                    )
                                                    
                                                    # Handle user reassignment
                                                    if new_user != current_user:
                                                        try:
                                                            with engine.connect() as conn:
                                                                # Update user assignment in database
                                                                new_user_value = new_user if new_user != "Not set" else None
                                                                conn.execute(text('''
                                                                    UPDATE trello_time_tracking 
                                                                    SET user_name = :new_user
                                                                    WHERE card_name = :card_name 
                                                                    AND list_name = :list_name 
                                                                    AND user_name = :old_user
                                                                '''), {
                                                                    'new_user': new_user_value,
                                                                    'card_name': book_title,
                                                                    'list_name': stage_name,
                                                                    'old_user': user_name
                                                                })
                                                                conn.commit()
                                                            st.success(f"User reassigned to {new_user}")
                                                            st.rerun()
                                                        except Exception as e:
                                                            st.error(f"Error reassigning user: {str(e)}")
                                                    
                                                    st.write(f"**Progress:** {format_seconds_to_time(actual_time)}/{format_seconds_to_time(estimated_time_for_user)}")
                                                    
                                                    # Progress bar
                                                    progress_percentage = (actual_time / estimated_time_for_user) if estimated_time_for_user > 0 else 0
                                                    st.progress(min(progress_percentage, 1.0))
                                                    
                                                    if progress_percentage > 1.0:
                                                        st.write(f"{(progress_percentage - 1) * 100:.1f}% over estimate")
                                                    elif progress_percentage == 1.0:
                                                        st.write("COMPLETE: 100%")
                                                    else:
                                                        st.write(f"{progress_percentage * 100:.1f}% complete")
                                                    
                                                    # Completion checkbox
                                                    is_completed = get_task_completion(engine, book_title, user_name, stage_name)
                                                    new_completion_status = st.checkbox(
                                                        "Completed",
                                                        value=is_completed,
                                                        key=f"complete_{book_title}_{stage_name}_{user_name}"
                                                    )
                                                    
                                                    # Update completion status if changed
                                                    if new_completion_status != is_completed:
                                                        update_task_completion(engine, book_title, user_name, stage_name, new_completion_status)
                                                        st.rerun()
                                            
                                            with col2:
                                                # Empty space - timer moved to button column
                                                st.write("")
                                            
                                            with col3:
                                                # Start/Stop timer button with timer display
                                                if task_key not in st.session_state.timers:
                                                    st.session_state.timers[task_key] = False
                                                
                                                # Create columns for button and timer with better spacing
                                                btn_col, timer_col = st.columns([1, 2])
                                                
                                                with btn_col:
                                                    if st.session_state.timers[task_key]:
                                                        if st.button("Stop", key=f"stop_{task_key}"):
                                                            # Store scroll position before stopping timer
                                                            st.markdown("""
                                                            <script>
                                                            sessionStorage.setItem('scrollPosition', window.pageYOffset);
                                                            </script>
                                                            """, unsafe_allow_html=True)
                                                            
                                                            # Keep the book card and stage expanded after stopping timer
                                                            expanded_key = f"expanded_{book_title}"
                                                            st.session_state[expanded_key] = True
                                                            
                                                            # Also keep the stage expanded
                                                            stage_expanded_key = f"stage_expanded_{book_title}_{stage_name}"
                                                            st.session_state[stage_expanded_key] = True
                                                            
                                                            # Stop timer and add time to database
                                                            if task_key in st.session_state.timer_start_times:
                                                                # Use consistent UTC-based calculation
                                                                current_time = datetime.utcnow().replace(tzinfo=timezone.utc).astimezone(BST)
                                                                elapsed = current_time - st.session_state.timer_start_times[task_key]
                                                                elapsed_seconds = int(elapsed.total_seconds())
                                                                
                                                                # Add elapsed time to database
                                                                try:
                                                                    # Get board name from original data
                                                                    user_original_data = stage_data[stage_data['User'] == user_name].iloc[0]
                                                                    board_name = user_original_data['Board']
                                                                    # Get existing tag from original data
                                                                    existing_tag = user_original_data.get('Tag', None) if 'Tag' in user_original_data else None
                                                                    
                                                                    with engine.connect() as conn:
                                                                        conn.execute(text('''
                                                                            INSERT INTO trello_time_tracking 
                                                                            (card_name, user_name, list_name, time_spent_seconds, board_name, created_at, session_start_time, tag)
                                                                            VALUES (:card_name, :user_name, :list_name, :time_spent_seconds, :board_name, :created_at, :session_start_time, :tag)
                                                                        '''), {
                                                                            'card_name': book_title,
                                                                            'user_name': user_name if user_name != "Not set" else None,
                                                                            'list_name': stage_name,
                                                                            'time_spent_seconds': elapsed_seconds,
                                                                            'board_name': board_name,
                                                                            'created_at': datetime.now(BST),
                                                                            'session_start_time': st.session_state.timer_start_times[task_key],
                                                                            'tag': existing_tag
                                                                        })
                                                                        conn.commit()
                                                                    
                                                                    # Remove from persistent storage
                                                                    remove_active_timer(engine, task_key)
                                                                    
                                                                    st.session_state.timers[task_key] = False
                                                                    del st.session_state.timer_start_times[task_key]
                                                                    st.rerun()
                                                                    
                                                                except Exception as e:
                                                                    st.error(f"Error saving time: {str(e)}")
                                                    else:
                                                        if st.button("Start", key=f"start_{task_key}"):
                                                            # Start timer and save to persistent storage
                                                            # Ensure we're using BST (UTC+1) consistently
                                                            utc_time = datetime.utcnow()
                                                            start_time = utc_time.replace(tzinfo=timezone.utc).astimezone(BST)
                                                            st.session_state.timers[task_key] = True
                                                            st.session_state.timer_start_times[task_key] = start_time
                                                            
                                                            # Save to persistent storage
                                                            user_original_data = stage_data[stage_data['User'] == user_name].iloc[0]
                                                            board_name = user_original_data['Board']
                                                            
                                                            save_active_timer(
                                                                engine, task_key, book_title, 
                                                                user_name if user_name != "Not set" else None,
                                                                stage_name, board_name, start_time
                                                            )
                                                            
                                                            st.rerun()
                                                
                                                with timer_col:
                                                    # Show "Recording" text when timer is running
                                                    if st.session_state.timers[task_key] and task_key in st.session_state.timer_start_times:
                                                        start_time = st.session_state.timer_start_times[task_key]
                                                        # Ensure timezone-aware datetime for calculations
                                                        if start_time.tzinfo is None:
                                                            start_time = start_time.replace(tzinfo=BST)
                                                        elif start_time.tzinfo != BST:
                                                            # Convert to BST if it's in a different timezone
                                                            start_time = start_time.astimezone(BST)
                                                        
                                                        # Calculate and display current elapsed time
                                                        # Use UTC time and convert to BST for consistent calculation
                                                        current_time = datetime.utcnow().replace(tzinfo=timezone.utc).astimezone(BST)
                                                        elapsed = current_time - start_time
                                                        elapsed_str = str(elapsed).split('.')[0]  # Remove microseconds
                                                        
                                                        st.write(f"**Recording** ({elapsed_str})")
                                                        
                                                        # Add refresh button with scroll position preservation
                                                        refresh_key = f"refresh_timer_{task_key}"
                                                        if st.button("refresh", key=refresh_key, type="primary", help="Refresh timer display"):
                                                            # Store scroll position before refresh
                                                            st.markdown("""
                                                            <script>
                                                            // Store scroll position before page refresh
                                                            sessionStorage.setItem('scrollPosition', window.pageYOffset);
                                                            </script>
                                                            """, unsafe_allow_html=True)
                                                            st.rerun()
                                                        
                                                        # Restore scroll position after refresh
                                                        st.markdown("""
                                                        <script>
                                                        // Restore scroll position after page refresh
                                                        window.addEventListener('load', function() {
                                                            const scrollPos = sessionStorage.getItem('scrollPosition');
                                                            if (scrollPos) {
                                                                window.scrollTo(0, parseInt(scrollPos));
                                                                sessionStorage.removeItem('scrollPosition');
                                                            }
                                                        });
                                                        </script>
                                                        """, unsafe_allow_html=True)
                                                        
                                                        # Add JavaScript for localStorage persistence
                                                        st.markdown(f"""
                                                        <script>
                                                        // Store active timer in localStorage
                                                        localStorage.setItem('activeTimer_{task_key}', '{start_time.isoformat()}');
                                                        </script>
                                                        """, unsafe_allow_html=True)
                                                    else:
                                                        st.write("")
                                                
                                                # Manual time entry section
                                                st.write("**Manual Entry:**")
                                                
                                                # Create a form to handle Enter key properly
                                                with st.form(key=f"time_form_{task_key}"):
                                                    manual_time = st.text_input(
                                                        "Add time (hh:mm:ss):", 
                                                        placeholder="01:30:00"
                                                    )
                                                    
                                                    # Hide the submit button and form styling with CSS
                                                    st.markdown("""
                                                    <style>
                                                    div[data-testid="stForm"] button {
                                                        display: none;
                                                    }
                                                    div[data-testid="stForm"] {
                                                        border: none !important;
                                                        background: none !important;
                                                        padding: 0 !important;
                                                    }
                                                    </style>
                                                    """, unsafe_allow_html=True)
                                                    
                                                    submitted = st.form_submit_button("Add Time")
                                                    
                                                    if submitted and manual_time:
                                                        try:
                                                            # Parse the time format hh:mm:ss
                                                            time_parts = manual_time.split(':')
                                                            if len(time_parts) == 3:
                                                                hours = int(time_parts[0])
                                                                minutes = int(time_parts[1])
                                                                seconds = int(time_parts[2])
                                                                total_seconds = hours * 3600 + minutes * 60 + seconds
                                                                
                                                                if total_seconds > 0:
                                                                    # Add manual time to database
                                                                    try:
                                                                        # Get board name from original data
                                                                        user_original_data = stage_data[stage_data['User'] == user_name].iloc[0]
                                                                        board_name = user_original_data['Board']
                                                                        # Get existing tag from original data
                                                                        existing_tag = user_original_data.get('Tag', None) if 'Tag' in user_original_data else None
                                                                        
                                                                        with engine.connect() as conn:
                                                                            conn.execute(text('''
                                                                                INSERT INTO trello_time_tracking 
                                                                                (card_name, user_name, list_name, time_spent_seconds, board_name, created_at, tag)
                                                                                VALUES (:card_name, :user_name, :list_name, :time_spent_seconds, :board_name, :created_at, :tag)
                                                                            '''), {
                                                                                'card_name': book_title,
                                                                                'user_name': user_name,
                                                                                'list_name': stage_name,
                                                                                'time_spent_seconds': total_seconds,
                                                                                'board_name': board_name,
                                                                                'created_at': datetime.now(BST),
                                                                                'tag': existing_tag
                                                                            })
                                                                            conn.commit()
                                                                        
                                                                        st.success(f"Added {manual_time} to progress")
                                                                        st.rerun()
                                                                        
                                                                    except Exception as e:
                                                                        st.error(f"Error saving time: {str(e)}")
                                                                else:
                                                                    st.error("Time must be greater than 00:00:00")
                                                            else:
                                                                st.error("Please use format hh:mm:ss (e.g., 01:30:00)")
                                                        except ValueError:
                                                            st.error("Please enter valid numbers in hh:mm:ss format")
                                                
                                                # Add Remove stage button at the bottom right of the column
                                                if st.button("Remove stage", key=f"remove_{book_title}_{stage_name}_{user_name}", type="secondary"):
                                                    # Single click delete
                                                    if delete_task_stage(engine, book_title, user_name, stage_name):
                                                        st.success(f"Removed {stage_name} for {user_name}")
                                                        st.rerun()
                                                    else:
                                                        st.error("Failed to remove stage")

                                
                                # Show count of running timers (refresh buttons now appear under individual timers)
                                running_timers = [k for k, v in st.session_state.timers.items() if v and book_title in k]
                                if running_timers:
                                    st.write(f"{len(running_timers)} timer(s) running")
                                
                                # Add stage dropdown
                                available_stages = get_available_stages_for_book(engine, book_title)
                                if available_stages:
                                    st.markdown("---")
                                    selected_stage = st.selectbox(
                                        "Add stage:",
                                        options=["Select a stage to add..."] + available_stages,
                                        key=f"add_stage_{book_title}"
                                    )
                                    
                                    if selected_stage != "Select a stage to add...":
                                        # Get book info for board name and tag
                                        book_info = next((book for book in all_books if book[0] == book_title), None)
                                        board_name = book_info[1] if book_info else None
                                        tag = book_info[2] if book_info else None
                                        
                                        if add_stage_to_book(engine, book_title, selected_stage, board_name, tag):
                                            st.success(f"Added {selected_stage} to {book_title}")
                                            st.rerun()
                                        else:
                                            st.error("Failed to add stage")
                                
                                # Archive and Delete buttons at the bottom of each book
                                st.markdown("---")
                                col1, col2 = st.columns(2)
                                
                                with col1:
                                    if st.button(f"Archive '{book_title}'", key=f"archive_{book_title}", help="Move this book to archive"):
                                        try:
                                            with engine.connect() as conn:
                                                # Add archived field to database if it doesn't exist
                                                conn.execute(text('''
                                                    UPDATE trello_time_tracking 
                                                    SET archived = TRUE 
                                                    WHERE card_name = :card_name
                                                '''), {'card_name': book_title})
                                                conn.commit()
                                            
                                            # Keep user on the current tab
                                            st.session_state.active_tab = 0  # Book Progress tab
                                            st.success(f"'{book_title}' has been archived successfully!")
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"Error archiving book: {str(e)}")
                                
                                with col2:
                                    if st.button(f"Delete '{book_title}'", key=f"delete_progress_{book_title}", help="Permanently delete this book and all its data", type="secondary"):
                                        # Add confirmation using session state
                                        confirm_key = f"confirm_delete_progress_{book_title}"
                                        if confirm_key not in st.session_state:
                                            st.session_state[confirm_key] = False
                                        
                                        if not st.session_state[confirm_key]:
                                            st.session_state[confirm_key] = True
                                            st.warning(f"Click 'Delete {book_title}' again to permanently delete all data for this book.")
                                            st.rerun()
                                        else:
                                            try:
                                                with engine.connect() as conn:
                                                    conn.execute(text('''
                                                        DELETE FROM trello_time_tracking 
                                                        WHERE card_name = :card_name
                                                    '''), {'card_name': book_title})
                                                    conn.commit()
                                                
                                                # Reset confirmation state
                                                del st.session_state[confirm_key]
                                                # Keep user on the Book Progress tab
                                                st.session_state.active_tab = 0  # Book Progress tab
                                                st.success(f"'{book_title}' has been permanently deleted!")
                                                st.rerun()
                                            except Exception as e:
                                                st.error(f"Error deleting book: {str(e)}")
                                                # Reset confirmation state on error
                                                if confirm_key in st.session_state:
                                                    del st.session_state[confirm_key]
                                        
                                        stage_counter += 1
                    else:
                        if search_query:
                            st.warning(f"No books found matching '{search_query}'")
                        else:
                            st.warning("No book completion data available")
                else:
                    st.warning("No data available in database")
            else:
                st.info("No data available. Please add entries in the 'Data Entry' tab.")
                
        except Exception as e:
            st.error(f"Error accessing database: {str(e)}")
    
    elif selected_tab == "Reporting":
        st.header("Reporting")
        st.markdown("Filter tasks by user, book, board, tag, and date range from all uploaded data.")
        
        # Get filter options from database
        users = get_users_from_database(engine)
        books = get_books_from_database(engine)
        boards = get_boards_from_database(engine)
        tags = get_tags_from_database(engine)
        
        if not users:
            st.info("No users found in database. Please add entries in the 'Add Book' tab first.")
            return
        
        # Filter selection - organized in columns
        col1, col2 = st.columns(2)
        
        with col1:
            # User selection dropdown
            selected_user = st.selectbox(
                "Select User:",
                options=["All Users"] + users,
                help="Choose a user to view their tasks"
            )
            
            # Book search input
            book_search = st.text_input(
                "Search Book (optional):",
                placeholder="Start typing to search books...",
                help="Type to search for a specific book"
            )
            # Match the search to available books
            if book_search:
                matched_books = [book for book in books if book_search.lower() in book.lower()]
                if matched_books:
                    selected_book = st.selectbox(
                        "Select from matches:",
                        options=matched_books,
                        help="Choose from matching books"
                    )
                else:
                    st.warning("No books found matching your search")
                    selected_book = "All Books"
            else:
                selected_book = "All Books"
        
        with col2:
            # Board selection dropdown
            selected_board = st.selectbox(
                "Select Board (optional):",
                options=["All Boards"] + boards,
                help="Choose a specific board to filter by"
            )
            
            # Tag selection dropdown
            selected_tag = st.selectbox(
                "Select Tag (optional):",
                options=["All Tags"] + tags,
                help="Choose a specific tag to filter by"
            )
        
        # Date range selection
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input(
                "Start Date (optional):",
                value=None,
                help="Leave empty to include all dates"
            )
        
        with col2:
            end_date = st.date_input(
                "End Date (optional):",
                value=None,
                help="Leave empty to include all dates"
            )
        
        # Update button
        update_button = st.button("Update Table", type="primary")
        
        # Validate date range
        if start_date and end_date and start_date > end_date:
            st.error("Start date must be before end date")
            return
        
        # Filter and display results only when button is clicked or on initial load
        if update_button or 'filtered_tasks_displayed' not in st.session_state:
            with st.spinner("Loading filtered tasks..."):
                filtered_tasks = get_filtered_tasks_from_database(
                    engine, 
                    user_name=selected_user if selected_user != "All Users" else None,
                    book_name=selected_book if selected_book != "All Books" else None,
                    board_name=selected_board if selected_board != "All Boards" else None,
                    tag_name=selected_tag if selected_tag != "All Tags" else None,
                    start_date=start_date, 
                    end_date=end_date
                )
            
            # Store in session state to prevent automatic reloading
            st.session_state.filtered_tasks_displayed = True
            st.session_state.current_filtered_tasks = filtered_tasks
            st.session_state.current_filters = {
                'user': selected_user,
                'book': selected_book,
                'board': selected_board,
                'tag': selected_tag,
                'start_date': start_date,
                'end_date': end_date
            }
        
        # Display cached results if available
        if 'current_filtered_tasks' in st.session_state:
            
            filtered_tasks = st.session_state.current_filtered_tasks
            current_filters = st.session_state.get('current_filters', {})
            
            if not filtered_tasks.empty:
                st.subheader("Filtered Results")
                
                # Show active filters info
                active_filters = []
                if current_filters.get('user') and current_filters.get('user') != "All Users":
                    active_filters.append(f"User: {current_filters.get('user')}")
                if current_filters.get('book') and current_filters.get('book') != "All Books":
                    active_filters.append(f"Book: {current_filters.get('book')}")
                if current_filters.get('board') and current_filters.get('board') != "All Boards":
                    active_filters.append(f"Board: {current_filters.get('board')}")
                if current_filters.get('tag') and current_filters.get('tag') != "All Tags":
                    active_filters.append(f"Tag: {current_filters.get('tag')}")
                if current_filters.get('start_date') or current_filters.get('end_date'):
                    start_str = current_filters.get('start_date').strftime('%d/%m/%Y') if current_filters.get('start_date') else 'All'
                    end_str = current_filters.get('end_date').strftime('%d/%m/%Y') if current_filters.get('end_date') else 'All'
                    active_filters.append(f"Date range: {start_str} to {end_str}")
                
                if active_filters:
                    st.info("Active filters: " + " | ".join(active_filters))
                
                st.dataframe(
                    filtered_tasks,
                    use_container_width=True,
                    hide_index=True
                )
                
                # Download button for filtered results
                csv_buffer = io.StringIO()
                filtered_tasks.to_csv(csv_buffer, index=False)
                st.download_button(
                    label="Download Filtered Results",
                    data=csv_buffer.getvalue(),
                    file_name="filtered_tasks.csv",
                    mime="text/csv"
                )
                
                # Summary statistics for filtered data
                st.subheader("Summary")
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    st.metric("Total Books", int(filtered_tasks['Book Title'].nunique()))
                
                with col2:
                    st.metric("Total Tasks", len(filtered_tasks))
                
                with col3:
                    st.metric("Unique Users", int(filtered_tasks['User'].nunique()))
                
                with col4:
                    # Calculate total time from formatted time strings
                    total_seconds = 0
                    for time_str in filtered_tasks['Time Spent']:
                        if time_str != "00:00:00":
                            parts = time_str.split(':')
                            total_seconds += int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                    total_hours = total_seconds / 3600
                    st.metric("Total Time (Hours)", f"{total_hours:.1f}")
            
            else:
                st.warning("No tasks found matching the selected filters.")
        
        elif 'filtered_tasks_displayed' not in st.session_state:
            st.info("Click 'Update Table' to load filtered results.")
    
    elif selected_tab == "Archive":
        st.header("Archive")
        st.markdown("View and manage archived books.")
        
        try:
            # Get count of archived records
            with engine.connect() as conn:
                archived_count = conn.execute(text('SELECT COUNT(*) FROM trello_time_tracking WHERE archived = TRUE')).scalar()
            
            if archived_count and archived_count > 0:
                st.info(f"Showing archived books from {archived_count} database records.")
                
                # Get archived data from database
                df_archived = pd.read_sql(
                    '''SELECT card_name as "Card name", 
                       COALESCE(user_name, 'Not set') as "User", 
                       list_name as "List", 
                       time_spent_seconds as "Time spent (s)", 
                       date_started as "Date started (f)", 
                       card_estimate_seconds as "Card estimate(s)", 
                       board_name as "Board", created_at, tag as "Tag"
                       FROM trello_time_tracking WHERE archived = TRUE ORDER BY created_at DESC''', 
                    engine
                )
                
                if not df_archived.empty:
                    # Add search bar for archived book titles
                    archive_search_query = st.text_input(
                        "Search archived books by title:",
                        placeholder="Enter book title to filter archived results...",
                        help="Search for specific archived books by typing part of the title",
                        key="archive_search"
                    )
                    
                    # Filter archived books based on search
                    filtered_archived_df = df_archived.copy()
                    if archive_search_query:
                        mask = filtered_archived_df['Card name'].str.contains(archive_search_query, case=False, na=False)
                        filtered_archived_df = filtered_archived_df[mask]
                    
                    # Get unique archived books
                    unique_archived_books = filtered_archived_df['Card name'].unique()
                    
                    if len(unique_archived_books) > 0:
                        st.write(f"Found {len(unique_archived_books)} archived books to display")
                        
                        # Display each archived book with same structure as Book Completion
                        for book_title in unique_archived_books:
                            book_mask = filtered_archived_df['Card name'] == book_title
                            book_data = filtered_archived_df[book_mask].copy()
                            
                            # Calculate overall progress
                            total_time_spent = book_data['Time spent (s)'].sum()
                            
                            # Calculate total estimated time
                            estimated_time = 0
                            if 'Card estimate(s)' in book_data.columns:
                                book_estimates = book_data['Card estimate(s)'].fillna(0).sum()
                                if book_estimates > 0:
                                    estimated_time = book_estimates
                            
                            # Calculate completion percentage and progress text
                            if estimated_time > 0:
                                completion_percentage = (total_time_spent / estimated_time) * 100
                                progress_text = f"{format_seconds_to_time(total_time_spent)}/{format_seconds_to_time(estimated_time)} ({completion_percentage:.1f}%)"
                            else:
                                completion_percentage = 0
                                progress_text = f"Total: {format_seconds_to_time(total_time_spent)} (No estimate)"
                            
                            with st.expander(book_title, expanded=False):
                                # Show progress bar and completion info at the top
                                progress_bar_html = f"""
                                <div style="width: 50%; background-color: #f0f0f0; border-radius: 5px; height: 10px; margin: 8px 0;">
                                    <div style="width: {min(completion_percentage, 100):.1f}%; background-color: #007bff; height: 100%; border-radius: 5px;"></div>
                                </div>
                                """
                                st.markdown(progress_bar_html, unsafe_allow_html=True)
                                st.markdown(f'<div style="font-size: 14px; color: #666; margin-bottom: 10px;">{progress_text}</div>', unsafe_allow_html=True)
                                
                                st.markdown("---")
                                
                                # Show task breakdown for archived book
                                task_breakdown = book_data.groupby(['List', 'User'])['Time spent (s)'].sum().reset_index()
                                task_breakdown['Time Spent'] = task_breakdown['Time spent (s)'].apply(format_seconds_to_time)
                                task_breakdown = task_breakdown[['List', 'User', 'Time Spent']]
                                
                                st.write("**Task Breakdown:**")
                                st.dataframe(task_breakdown, use_container_width=True, hide_index=True)
                                
                                # Unarchive and Delete buttons
                                st.markdown("---")
                                col1, col2 = st.columns(2)
                                
                                with col1:
                                    if st.button(f"Unarchive '{book_title}'", key=f"unarchive_{book_title}", help="Move this book back to active books"):
                                        try:
                                            with engine.connect() as conn:
                                                conn.execute(text('''
                                                    UPDATE trello_time_tracking 
                                                    SET archived = FALSE 
                                                    WHERE card_name = :card_name
                                                '''), {'card_name': book_title})
                                                conn.commit()
                                            
                                            # Keep user on the Archive tab
                                            st.session_state.active_tab = 2  # Archive tab
                                            st.success(f"'{book_title}' has been unarchived successfully!")
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"Error unarchiving book: {str(e)}")
                                
                                with col2:
                                    if st.button(f"Delete '{book_title}'", key=f"delete_{book_title}", help="Permanently delete this book and all its data", type="secondary"):
                                        # Add confirmation using session state
                                        confirm_key = f"confirm_delete_{book_title}"
                                        if confirm_key not in st.session_state:
                                            st.session_state[confirm_key] = False
                                        
                                        if not st.session_state[confirm_key]:
                                            st.session_state[confirm_key] = True
                                            st.warning(f"Click 'Delete {book_title}' again to permanently delete all data for this book.")
                                            st.rerun()
                                        else:
                                            try:
                                                with engine.connect() as conn:
                                                    conn.execute(text('''
                                                        DELETE FROM trello_time_tracking 
                                                        WHERE card_name = :card_name
                                                    '''), {'card_name': book_title})
                                                    conn.commit()
                                                
                                                # Reset confirmation state
                                                del st.session_state[confirm_key]
                                                # Keep user on the Archive tab
                                                st.session_state.active_tab = 2  # Archive tab
                                                st.success(f"'{book_title}' has been permanently deleted!")
                                                st.rerun()
                                            except Exception as e:
                                                st.error(f"Error deleting book: {str(e)}")
                                                # Reset confirmation state on error
                                                if confirm_key in st.session_state:
                                                    del st.session_state[confirm_key]
                    else:
                        if archive_search_query:
                            st.warning(f"No archived books found matching '{archive_search_query}'")
                        else:
                            st.warning("No archived books available")
                else:
                    st.warning("No archived books available")
            else:
                st.info("No archived books found. Archive books from the 'Book Completion' tab to see them here.")
                
        except Exception as e:
            st.error(f"Error accessing archived data: {str(e)}")



if __name__ == "__main__":
    main()

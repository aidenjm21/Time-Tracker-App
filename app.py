import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from collections import Counter
import io
import os
import re
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(card_name, user_name, list_name, date_started, time_spent_seconds)
                )
            '''))
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
            result = conn.execute(text('SELECT DISTINCT user_name FROM trello_time_tracking ORDER BY user_name'))
            return [row[0] for row in result]
    except Exception as e:
        st.error(f"Error fetching users: {str(e)}")
        return []

def get_user_tasks_from_database(_engine, user_name, start_date=None, end_date=None):
    """Get user tasks from database with optional date filtering"""
    try:
        query = '''
            SELECT card_name, list_name, date_started, SUM(time_spent_seconds) as total_time
            FROM trello_time_tracking 
            WHERE user_name = :user_name
        '''
        params = {'user_name': user_name}
        
        if start_date:
            query += ' AND date_started >= :start_date'
            params['start_date'] = start_date
        
        if end_date:
            query += ' AND date_started <= :end_date'
            params['end_date'] = end_date
        
        query += ' GROUP BY card_name, list_name, date_started ORDER BY card_name, list_name'
        
        with _engine.connect() as conn:
            result = conn.execute(text(query), params)
            data = []
            for row in result:
                data.append({
                    'Book Title': row[0],
                    'List': row[1],
                    'Date': row[2].strftime('%d/%m/%Y') if row[2] else 'N/A',
                    'Time Spent': format_seconds_to_time(row[3])
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
    
    # Create tabs for different views
    tab1, tab2, tab3 = st.tabs(["Data Entry", "Book Completion", "Filter User Tasks"])
    
    with tab1:
        # Manual Data Entry Form
        st.header("Manual Data Entry")
        st.markdown("Add individual time tracking entries for detailed stage-specific analysis.")
        
        # General fields
        col1, col2 = st.columns(2)
        with col1:
            card_name = st.text_input("Card Name", placeholder="Enter book title", key="manual_card_name")
        with col2:
            board_name = st.text_input("Board", placeholder="Enter board name", key="manual_board_name")
            
        st.subheader("Task Assignment & Estimates")
        st.markdown("*Assign users to stages and set time estimates. All tasks start with 0 actual time - use the Book Completion tab to track actual work time.*")
        
        # Define user groups for different types of work (alphabetically ordered)
        editorial_users = ["None", "Bethany Latham", "Charis Mather", "Noah Leatherland", "Rebecca Phillips-Bartlett"]
        design_users = ["None", "Amelia Harris", "Amy Li", "Drue Rintoul", "Jasmine Pointer", "Ker Ker Lee", "Rob Delph"]
        
        # Time tracking fields with specific user groups
        time_fields = [
            ("Editorial R&D Time", "Editorial R&D", editorial_users),
            ("Editorial Writing", "Editorial Writing", editorial_users),
            ("1st Proof", "1st Proof", editorial_users),
            ("2nd Proof", "2nd Proof", editorial_users),
            ("3rd Proof", "3rd Proof", editorial_users),
            ("4th Proof", "4th Proof", editorial_users),
            ("5th Proof", "5th Proof", editorial_users),
            ("Editorial Sign Off", "Editorial Sign Off", editorial_users),
            ("Cover Design", "Cover Design", design_users),
            ("Design Time", "Design Time", design_users),
            ("Design Sign Off", "Design Sign Off", design_users)
        ]
        
        # Calculate and display time estimations in real-time
        editorial_total = 0.0
        design_total = 0.0
        time_entries = {}
        
        editorial_fields = ["Editorial R&D", "Editorial Writing", "1st Proof", "2nd Proof", "3rd Proof", "4th Proof", "5th Proof", "Editorial Sign Off"]
        design_fields = ["Cover Design", "Design Time", "Design Sign Off"]
        
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
            if selected_user != "None":
                final_user = selected_user
                
                # Store the entry if both user and time are provided
                if final_user and time_value > 0:
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
        
        # Submit button outside of form
        if st.button("Add Entry", type="primary", key="manual_submit"):
            if not card_name:
                st.error("Please fill in Card Name field")
            elif not time_entries:
                st.error("Please add at least one time entry with a user assigned")
            else:
                try:
                    entries_added = 0
                    current_time = datetime.now()
                    
                    with engine.connect() as conn:
                        for list_name, entry_data in time_entries.items():
                            # Create task entry with 0 time spent - users will use timer to track actual time
                            # The time_hours value from the form is just for estimation display, not actual time spent
                            
                            # Convert hours to seconds for estimate
                            estimate_seconds = int(entry_data['time_hours'] * 3600)
                            
                            # Insert into database with 0 time spent but store the estimate
                            conn.execute(text('''
                                INSERT INTO trello_time_tracking 
                                (card_name, user_name, list_name, time_spent_seconds, card_estimate_seconds, board_name, created_at)
                                VALUES (:card_name, :user_name, :list_name, :time_spent_seconds, :card_estimate_seconds, :board_name, :created_at)
                            '''), {
                                'card_name': card_name,
                                'user_name': entry_data['user'],
                                'list_name': list_name,
                                'time_spent_seconds': 0,  # Start with 0 time spent
                                'card_estimate_seconds': estimate_seconds,  # Store the estimate
                                'board_name': board_name if board_name else 'Manual Entry',
                                'created_at': current_time
                            })
                            entries_added += 1
                        
                        conn.commit()
                    
                    if entries_added > 0:
                        st.success(f"Successfully created {entries_added} task assignments for '{card_name}'. All tasks start with 0:00:00 time - use Book Completion tab to track actual work.")
                        st.rerun()
                    else:
                        st.warning("No tasks created - please assign users to stages (time estimates are optional)")
                        
                except Exception as e:
                    st.error(f"Error adding manual entry: {str(e)}")
    
    with tab2:
        st.header("Book Completion Progress")
        st.markdown("Visual progress tracking for all books with individual task timers.")
        
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
                
                # Get data from database for book completion
                df_from_db = pd.read_sql(
                    '''SELECT card_name as "Card name", user_name as "User", list_name as "List", 
                       time_spent_seconds as "Time spent (s)", date_started as "Date started (f)", 
                       card_estimate_seconds as "Card estimate(s)", board_name as "Board", created_at 
                       FROM trello_time_tracking ORDER BY created_at DESC''', 
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
                    
                    # Get unique books
                    unique_books = filtered_df['Card name'].unique()
                    
                    if len(unique_books) > 0:
                        st.write(f"Found {len(unique_books)} books to display")
                        
                        # Display each book with enhanced visualization
                        for book_title in unique_books:
                            book_mask = filtered_df['Card name'] == book_title
                            book_data = filtered_df[book_mask].copy()
                            
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
                                    'Editorial R&D': 2 * 3600,      # 2 hours default
                                    'Editorial Writing': 8 * 3600,   # 8 hours default 
                                    '1st Proof': 2 * 3600,          # 2 hours default
                                    '2nd Proof': 1.5 * 3600,        # 1.5 hours default
                                    '3rd Proof': 1 * 3600,          # 1 hour default
                                    '4th Proof': 1 * 3600,          # 1 hour default
                                    '5th Proof': 1 * 3600,          # 1 hour default
                                    'Editorial Sign Off': 0.5 * 3600, # 30 minutes default
                                    'Cover Design': 4 * 3600,       # 4 hours default
                                    'Design Time': 6 * 3600,        # 6 hours default
                                    'Design Sign Off': 0.5 * 3600   # 30 minutes default
                                }
                                unique_stages = book_data['List'].unique()
                                estimated_time = sum(default_stage_estimates.get(stage, 3600) for stage in unique_stages)
                            
                            # Create expandable section for each book
                            with st.expander(f"{book_title}", expanded=False):
                                # Overall progress bar
                                col1, col2 = st.columns([3, 1])
                                
                                with col1:
                                    if estimated_time > 0:
                                        completion_percentage = (total_time_spent / estimated_time) * 100
                                        st.progress(min(completion_percentage / 100, 1.0))
                                        st.write(f"**Overall Progress:** {format_seconds_to_time(total_time_spent)}/{format_seconds_to_time(estimated_time)} ({completion_percentage:.1f}%)")
                                    else:
                                        st.write(f"**Total Time:** {format_seconds_to_time(total_time_spent)} (No estimate)")
                                
                                with col2:
                                    st.metric("Total Tasks", len(book_data))
                                
                                st.markdown("---")
                                
                                # Define the order of stages to match the data entry form
                                stage_order = [
                                    'Editorial R&D', 'Editorial Writing', '1st Proof', '2nd Proof', 
                                    '3rd Proof', '4th Proof', '5th Proof', 'Editorial Sign Off',
                                    'Cover Design', 'Design Time', 'Design Sign Off'
                                ]
                                
                                # Group by stage/list and aggregate by user
                                stages_grouped = book_data.groupby('List')
                                
                                # Display stages in the defined order
                                stage_counter = 0
                                for stage_name in stage_order:
                                    if stage_name in stages_grouped.groups:
                                        stage_data = stages_grouped.get_group(stage_name)
                                        st.subheader(f"{stage_name}")
                                        
                                        # Aggregate time by user for this stage
                                        user_aggregated = stage_data.groupby('User')['Time spent (s)'].sum().reset_index()
                                        
                                        # Show one task per user for this stage
                                        for idx, user_task in user_aggregated.iterrows():
                                            user_name = user_task['User']
                                            actual_time = user_task['Time spent (s)']
                                            task_key = f"{book_title}_{stage_name}_{user_name}"
                                            
                                            # Get estimated time from the database for this specific user/stage combination
                                            user_stage_data = stage_data[stage_data['User'] == user_name]
                                            estimated_time = 3600  # Default 1 hour
                                            
                                            if not user_stage_data.empty and 'Card estimate(s)' in user_stage_data.columns:
                                                estimate_val = user_stage_data['Card estimate(s)'].iloc[0]
                                                if not pd.isna(estimate_val) and estimate_val > 0:
                                                    estimated_time = estimate_val
                                        
                                        # Task details container
                                        task_container = st.container()
                                        
                                        with task_container:
                                            # Create columns for task info and timer with better spacing
                                            col1, col2, col3 = st.columns([4, 1, 3])
                                            
                                            with col1:
                                                st.write(f"**User:** {user_name}")
                                                st.write(f"**Progress:** {format_seconds_to_time(actual_time)}/{format_seconds_to_time(estimated_time)}")
                                                
                                                # Progress bar
                                                progress_percentage = (actual_time / estimated_time) if estimated_time > 0 else 0
                                                st.progress(min(progress_percentage, 1.0))
                                                
                                                if progress_percentage > 1.0:
                                                    st.write(f"WARNING: {(progress_percentage - 1) * 100:.1f}% over estimate")
                                                else:
                                                    st.write(f"COMPLETE: {progress_percentage * 100:.1f}%")
                                            
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
                                                            # Stop timer and add time to database
                                                            if task_key in st.session_state.timer_start_times:
                                                                elapsed = datetime.now() - st.session_state.timer_start_times[task_key]
                                                                elapsed_seconds = int(elapsed.total_seconds())
                                                                
                                                                # Add elapsed time to database
                                                                try:
                                                                    # Get board name from original data
                                                                    user_original_data = stage_data[stage_data['User'] == user_name].iloc[0]
                                                                    board_name = user_original_data['Board']
                                                                    
                                                                    with engine.connect() as conn:
                                                                        conn.execute(text('''
                                                                            INSERT INTO trello_time_tracking 
                                                                            (card_name, user_name, list_name, time_spent_seconds, board_name, created_at)
                                                                            VALUES (:card_name, :user_name, :list_name, :time_spent_seconds, :board_name, :created_at)
                                                                        '''), {
                                                                            'card_name': book_title,
                                                                            'user_name': user_name,
                                                                            'list_name': stage_name,
                                                                            'time_spent_seconds': elapsed_seconds,
                                                                            'board_name': board_name,
                                                                            'created_at': datetime.now()
                                                                        })
                                                                        conn.commit()
                                                                    
                                                                    st.session_state.timers[task_key] = False
                                                                    del st.session_state.timer_start_times[task_key]
                                                                    st.rerun()
                                                                    
                                                                except Exception as e:
                                                                    st.error(f"Error saving time: {str(e)}")
                                                    else:
                                                        if st.button("Start", key=f"start_{task_key}"):
                                                            st.session_state.timers[task_key] = True
                                                            st.session_state.timer_start_times[task_key] = datetime.now()
                                                            st.rerun()
                                                
                                                with timer_col:
                                                    # Show "Recording" text when timer is running
                                                    if st.session_state.timers[task_key] and task_key in st.session_state.timer_start_times:
                                                        st.write("**Recording**")
                                                    else:
                                                        st.write("")
                                                
                                                # Manual time entry section
                                                st.write("**Manual Entry:**")
                                                manual_time = st.text_input(
                                                    "Add time (hh:mm:ss):", 
                                                    key=f"manual_{task_key}",
                                                    placeholder="01:30:00"
                                                )
                                                
                                                if st.button("Add Time", key=f"add_time_{task_key}"):
                                                    if manual_time:
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
                                                                        
                                                                        with engine.connect() as conn:
                                                                            conn.execute(text('''
                                                                                INSERT INTO trello_time_tracking 
                                                                                (card_name, user_name, list_name, time_spent_seconds, board_name, created_at)
                                                                                VALUES (:card_name, :user_name, :list_name, :time_spent_seconds, :board_name, :created_at)
                                                                            '''), {
                                                                                'card_name': book_title,
                                                                                'user_name': user_name,
                                                                                'list_name': stage_name,
                                                                                'time_spent_seconds': total_seconds,
                                                                                'board_name': board_name,
                                                                                'created_at': datetime.now()
                                                                            })
                                                                            conn.commit()
                                                                        
                                                                        st.rerun()
                                                                        
                                                                    except Exception as e:
                                                                        st.error(f"Error saving time: {str(e)}")
                                                                else:
                                                                    st.error("Time must be greater than 00:00:00")
                                                            else:
                                                                st.error("Please use format hh:mm:ss (e.g., 01:30:00)")
                                                        except ValueError:
                                                            st.error("Please enter valid numbers in hh:mm:ss format")
                                        
                                        st.markdown("---")
                                
                                # Show manual refresh button when timers are running
                                running_timers = [k for k, v in st.session_state.timers.items() if v and book_title in k]
                                if running_timers:
                                    st.write(f"{len(running_timers)} timer(s) running")
                                    if st.button("🔄 Refresh Timers", key=f"refresh_{book_title}"):
                                        st.rerun()
                                
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
    
    with tab3:
        st.header("Filter User Tasks")
        st.markdown("Filter tasks by user and date range from all uploaded data.")
        
        # Get users from database
        users = get_users_from_database(engine)
        
        if not users:
            st.info("No users found in database. Please add entries in the 'Data Entry' tab first.")
            return
        
        # User selection dropdown
        selected_user = st.selectbox(
            "Select User:",
            options=users,
            help="Choose a user to view their tasks"
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
        if selected_user and (update_button or 'user_tasks_displayed' not in st.session_state):
            with st.spinner("Loading user tasks..."):
                user_tasks = get_user_tasks_from_database(
                    engine, 
                    selected_user, 
                    start_date, 
                    end_date
                )
            
            # Store in session state to prevent automatic reloading
            st.session_state.user_tasks_displayed = True
            st.session_state.current_user_tasks = user_tasks
            st.session_state.current_user = selected_user
            st.session_state.current_start_date = start_date
            st.session_state.current_end_date = end_date
        
        # Display cached results if available
        if ('current_user_tasks' in st.session_state and 
            'current_user' in st.session_state and 
            st.session_state.current_user == selected_user):
            
            user_tasks = st.session_state.current_user_tasks
            display_start_date = st.session_state.get('current_start_date')
            display_end_date = st.session_state.get('current_end_date')
            
            if not user_tasks.empty:
                st.subheader(f"Tasks for {selected_user}")
                
                # Show date range info
                if display_start_date or display_end_date:
                    date_info = f"Date range: {display_start_date.strftime('%d/%m/%Y') if display_start_date else 'All'} to {display_end_date.strftime('%d/%m/%Y') if display_end_date else 'All'}"
                    st.info(date_info)
                
                st.dataframe(
                    user_tasks,
                    use_container_width=True,
                    hide_index=True
                )
                
                # Download button for filtered results
                csv_buffer = io.StringIO()
                user_tasks.to_csv(csv_buffer, index=False)
                st.download_button(
                    label=f"Download {selected_user}'s Tasks",
                    data=csv_buffer.getvalue(),
                    file_name=f"{selected_user}_tasks.csv",
                    mime="text/csv"
                )
                
                # Summary statistics for filtered data
                st.subheader("Summary")
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    st.metric("Total Books", int(user_tasks['Book Title'].nunique()))
                
                with col2:
                    st.metric("Total Tasks", len(user_tasks))
                
                with col3:
                    # Calculate total time from formatted time strings
                    total_seconds = 0
                    for time_str in user_tasks['Time Spent']:
                        if time_str != "00:00:00":
                            parts = time_str.split(':')
                            total_seconds += int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                    total_hours = total_seconds / 3600
                    st.metric("Total Time (Hours)", f"{total_hours:.1f}")
            
            else:
                st.warning(f"No tasks found for {selected_user} in the specified date range.")
        
        elif selected_user and 'user_tasks_displayed' not in st.session_state:
            st.info("Click 'Update Table' to load tasks for the selected user.")
    

if __name__ == "__main__":
    main()

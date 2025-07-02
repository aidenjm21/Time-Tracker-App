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

def save_to_database(df, engine):
    """Save CSV data to database using bulk operations, ignoring duplicates"""
    try:
        # Prepare all data first
        records_to_insert = []
        
        for _, row in df.iterrows():
            # Parse date if available
            date_started = None
            if 'Date started (f)' in df.columns and pd.notna(row['Date started (f)']):
                try:
                    date_started = pd.to_datetime(row['Date started (f)'], errors='coerce').date()
                except:
                    pass
            
            # Prepare data for insertion
            data = {
                'card_name': row['Card name'],
                'user_name': row['User'],
                'list_name': row['List'],
                'time_spent_seconds': int(row['Time spent (s)']),
                'date_started': date_started,
                'card_estimate_seconds': int(row.get('Card estimate(s)', 0)) if pd.notna(row.get('Card estimate(s)', 0)) else None,
                'board_name': row.get('Board', ''),
                'labels': row.get('Labels', '')
            }
            records_to_insert.append(data)
        
        # Use bulk insert with ON CONFLICT DO NOTHING for better performance
        records_added = 0
        with engine.connect() as conn:
            for data in records_to_insert:
                try:
                    result = conn.execute(text('''
                        INSERT INTO trello_time_tracking 
                        (card_name, user_name, list_name, time_spent_seconds, date_started, 
                         card_estimate_seconds, board_name, labels)
                        VALUES (:card_name, :user_name, :list_name, :time_spent_seconds, 
                                :date_started, :card_estimate_seconds, :board_name, :labels)
                        ON CONFLICT (card_name, user_name, list_name, date_started, time_spent_seconds) 
                        DO NOTHING
                    '''), data)
                    if result.rowcount > 0:
                        records_added += 1
                except:
                    continue
            conn.commit()
        
        return records_added
    except Exception as e:
        st.error(f"Error saving to database: {str(e)}")
        return 0

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

def validate_csv_columns(df):
    """Validate that the CSV has required columns"""
    required_columns = ['Card name', 'User', 'List', 'Time spent (s)']
    missing_columns = [col for col in required_columns if col not in df.columns]
    
    if missing_columns:
        return False, f"Missing required columns: {', '.join(missing_columns)}"
    
    return True, "Valid"

def main():
    st.title("Trello Time Tracking Analysis")
    st.markdown("Upload your Trello CSV export to analyse book production summaries and user task breakdowns.")
    
    # Initialise database
    engine = init_database()
    if not engine:
        st.error("Could not connect to database. Please check your configuration.")
        return
    
    # Create tabs for different views
    tab1, tab2, tab3, tab4 = st.tabs(["📁 Upload & Analyse CSV", "📊 Book Completion", "🔍 Filter User Tasks", "🗄️ Database Management"])
    
    with tab1:
        # Manual Data Entry Form
        st.header("📝 Manual Data Entry")
        st.markdown("Add individual time tracking entries for detailed stage-specific analysis.")
        
        with st.form("manual_entry_form"):
            # General fields
            col1, col2 = st.columns(2)
            with col1:
                card_name = st.text_input("Card Name", placeholder="Enter book title")
            with col2:
                board_name = st.text_input("Board", placeholder="Enter board name")
                
            st.subheader("Time Tracking Fields")
            st.markdown("*Assign different users to different stages. Leave time as 0 to skip a stage.*")
            
            # Define user groups for different types of work (alphabetically ordered)
            editorial_users = ["None", "Bethany Latham", "Charis Mather", "Noah Leatherland", "Rebecca Phillips-Bartlett"]
            design_users = ["None", "Amelia Harris", "Amy Li", "Drue Rintoul", "Jasmine Pointer", "Ker Ker Lee", "Rob Delph"]
            
            # Create a dictionary to store time entries
            time_entries = {}
            
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
                
                # Handle user selection
                if selected_user == "None":
                    final_user = None
                else:
                    final_user = selected_user
                
                # Store the entry if both user and time are provided
                if final_user and time_value > 0:
                    time_entries[list_name] = {
                        'user': final_user,
                        'time_hours': time_value
                    }
            
            # Calculate and display time estimations
            editorial_total = 0.0
            design_total = 0.0
            
            # Get current values from session state (Streamlit form widgets)
            editorial_fields = ["Editorial R&D", "Editorial Writing", "1st Proof", "2nd Proof", "3rd Proof", "4th Proof", "5th Proof", "Editorial Sign Off"]
            design_fields = ["Cover Design", "Design Time", "Design Sign Off"]
            
            for field_label, list_name, user_options in time_fields:
                time_key = f"time_{list_name.replace(' ', '_').lower()}"
                if time_key in st.session_state:
                    time_value = st.session_state[time_key]
                    if list_name in editorial_fields:
                        editorial_total += time_value
                    elif list_name in design_fields:
                        design_total += time_value
            
            total_estimation = editorial_total + design_total
            
            # Display calculations
            st.markdown("---")
            st.markdown("**Time Estimations:**")
            st.write(f"Editorial Time Estimation: {editorial_total:.1f} hours")
            st.write(f"Design Time Estimation: {design_total:.1f} hours")
            st.write(f"**Total Time Estimation: {total_estimation:.1f} hours**")
            st.markdown("---")
            
            # Submit button
            submitted = st.form_submit_button("➕ Add Entry", type="primary")
            
            if submitted:
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
                                # Convert hours to seconds
                                time_seconds = int(entry_data['time_hours'] * 3600)
                                
                                # Insert into database
                                conn.execute(text('''
                                    INSERT INTO trello_time_tracking 
                                    (card_name, user_name, list_name, time_spent_seconds, board_name, created_at)
                                    VALUES (:card_name, :user_name, :list_name, :time_spent_seconds, :board_name, :created_at)
                                '''), {
                                    'card_name': card_name,
                                    'user_name': entry_data['user'],
                                    'list_name': list_name,
                                    'time_spent_seconds': time_seconds,
                                    'board_name': board_name if board_name else 'Manual Entry',
                                    'created_at': current_time
                                })
                                entries_added += 1
                            
                            conn.commit()
                        
                        if entries_added > 0:
                            st.success(f"Successfully added {entries_added} entries for '{card_name}' with different users for each stage")
                            st.rerun()
                        else:
                            st.warning("No entries added - please enter time values greater than 0 with users assigned")
                            
                    except Exception as e:
                        st.error(f"Error adding manual entry: {str(e)}")
        
        st.markdown("---")  # Separator between manual entry and CSV upload
        
        # File upload
        st.header("📁 CSV File Upload")
        uploaded_file = st.file_uploader(
            "Choose a CSV file", 
            type="csv",
            help="Upload your Trello time tracking CSV export"
        )
    
        if uploaded_file is not None:
            try:
                # Read the CSV file
                df = pd.read_csv(uploaded_file)
                
                # Display basic info about the uploaded file
                st.success(f"Successfully loaded CSV with {len(df)} rows and {len(df.columns)} columns")
                
                # Validate required columns
                is_valid, validation_message = validate_csv_columns(df)
                
                if not is_valid:
                    st.error(f"Invalid CSV format: {validation_message}")
                    st.info("Required columns: Card name, User, List, Time spent (s)")
                    st.info("Optional columns: Card estimate(s)")
                    return
                
                # Save to database
                records_added = save_to_database(df, engine)
                
                if records_added > 0:
                    st.success(f"Added {records_added} new records to database (duplicates ignored)")
                else:
                    st.info("No new records added - all data already exists in database")
                
                # Display column names for reference
                with st.expander("View CSV Column Names"):
                    st.write("Columns found in your CSV:")
                    for col in df.columns:
                        st.write(f"• {col}")
                
                # Process and display Book Summary Table
                st.header("📚 Book Summary Table")
                book_summary = process_book_summary(df)
                
                if not book_summary.empty:
                    st.dataframe(
                        book_summary,
                        use_container_width=True,
                        hide_index=True
                    )
                    
                    # Add download button for book summary
                    csv_buffer = io.StringIO()
                    book_summary.to_csv(csv_buffer, index=False)
                    st.download_button(
                        label="📥 Download Book Summary as CSV",
                        data=csv_buffer.getvalue(),
                        file_name="book_summary.csv",
                        mime="text/csv"
                    )
                else:
                    st.warning("No data available for Book Summary Table")
                
                # Process and display User Task Breakdown
                st.header("👥 User Task Breakdown")
                user_breakdown = process_user_task_breakdown(df)
                
                if not user_breakdown.empty:
                    st.dataframe(
                        user_breakdown,
                        use_container_width=True,
                        hide_index=True
                    )
                    
                    # Add download button for user breakdown
                    csv_buffer = io.StringIO()
                    user_breakdown.to_csv(csv_buffer, index=False)
                    st.download_button(
                        label="📥 Download User Task Breakdown as CSV",
                        data=csv_buffer.getvalue(),
                        file_name="user_task_breakdown.csv",
                        mime="text/csv"
                    )
                else:
                    st.warning("No data available for User Task Breakdown Table")
                
                # Display summary statistics
                if not df.empty:
                    st.header("📊 Summary Statistics")
                    col1, col2, col3, col4 = st.columns(4)
                    
                    with col1:
                        st.metric("Total Books", int(df['Card name'].nunique()))
                    
                    with col2:
                        st.metric("Total Users", int(df['User'].nunique()))
                    
                    with col3:
                        total_time_hours = df['Time spent (s)'].sum() / 3600
                        st.metric("Total Time (Hours)", f"{total_time_hours:.1f}")
                    
                    with col4:
                        st.metric("Total Tasks", len(df))
            
            except Exception as e:
                st.error(f"Error processing CSV file: {str(e)}")
                st.info("Please ensure your CSV file is properly formatted and contains the required columns.")
        
        else:
            # Show instructions when no file is uploaded
            st.info("Please upload a CSV file to begin analysis.")
            
            with st.expander("📋 Expected CSV Format"):
                st.markdown("""
                Your Trello CSV export should contain the following columns:
                
                **Required columns:**
                - `Card name` - The book title
                - `User` - Team member name
                - `List` - Stage of process/task
                - `Time spent (s)` - Actual time spent in seconds
                
                **Optional columns:**
                - `Card estimate(s)` - Estimated creation time in seconds
                - `Date started (f)` - Date when the task was started in mm/dd/yyyy format (displayed as dd/mm/yyyy in User Task Breakdown)
                - `Board name` - Trello board name
                - `Labels` - Any labels associated with the card
                - Any other Trello export columns
                """)
    
    with tab2:
        st.header("📊 Book Completion Progress")
        st.markdown("Visual progress tracking for all books with search functionality.")
        
        # Check if we have data from database
        try:
            with engine.connect() as conn:
                result = conn.execute(text("SELECT COUNT(*) FROM trello_time_tracking"))
                total_records = result.scalar()
                
            if total_records and total_records > 0:
                st.info(f"Showing completion progress for books from {total_records} database records.")
                
                # Get data from database for book completion
                df_from_db = pd.read_sql(
                    'SELECT card_name as "Card name", user_name as "User", list_name as "List", time_spent_seconds as "Time spent (s)", date_started as "Date started (f)", card_estimate_seconds as "Card estimate(s)", board_name as "Board" FROM trello_time_tracking', 
                    engine
                )
                
                if not df_from_db.empty:
                    # Add search bar for book titles
                    search_query = st.text_input(
                        "🔍 Search books by title:",
                        placeholder="Enter book title to filter results...",
                        help="Search for specific books by typing part of the title",
                        key="completion_search"
                    )
                    
                    book_completion = process_book_completion(df_from_db, search_filter=search_query if search_query else None)
                    
                    if not book_completion.empty:
                        st.write(f"Found {len(book_completion)} books to display")
                        
                        # Display the visual progress for each book
                        for idx, row in book_completion.iterrows():
                            st.markdown(row['Visual Progress'], unsafe_allow_html=True)
                            st.markdown("---")  # Separator between books
                    else:
                        if search_query:
                            st.warning(f"No books found matching '{search_query}'")
                        else:
                            st.warning("No book completion data available")
                else:
                    st.warning("No data available in database")
            else:
                st.info("No data available. Please upload a CSV file first in the 'Upload & Analyse CSV' tab.")
                
        except Exception as e:
            st.error(f"Error accessing database: {str(e)}")
    
    with tab3:
        st.header("🔍 Filter User Tasks")
        st.markdown("Filter tasks by user and date range from all uploaded data.")
        
        # Get users from database
        users = get_users_from_database(engine)
        
        if not users:
            st.info("No users found in database. Please upload CSV data first.")
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
        update_button = st.button("🔄 Update Table", type="primary")
        
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
                    label=f"📥 Download {selected_user}'s Tasks",
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
    
    with tab4:
        st.header("🗄️ Database Management")
        st.markdown("View, edit, and manage all data in the database.")
        
        try:
            # Get total record count
            with engine.connect() as conn:
                result = conn.execute(text("SELECT COUNT(*) FROM trello_time_tracking"))
                total_records = result.scalar()
            
            if total_records and total_records > 0:
                st.info(f"Database contains {total_records} records")
                
                # Add filters for better management
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    # Filter by card name
                    card_filter = st.text_input(
                        "Filter by Card Name:",
                        placeholder="Enter card name to filter...",
                        key="db_card_filter"
                    )
                
                with col2:
                    # Filter by user
                    user_filter = st.selectbox(
                        "Filter by User:",
                        options=["All"] + get_users_from_database(engine),
                        key="db_user_filter"
                    )
                
                with col3:
                    # Limit number of records displayed
                    record_limit = st.selectbox(
                        "Records to display:",
                        options=[50, 100, 200, 500, "All"],
                        index=0,
                        key="db_record_limit"
                    )
                
                # Load data using a direct connection approach
                try:
                    with engine.connect() as conn:
                        if card_filter or user_filter != "All":
                            # Build filtered query
                            conditions = []
                            if card_filter:
                                safe_filter = card_filter.replace("'", "''")
                                conditions.append(f"card_name ILIKE '%{safe_filter}%'")
                            if user_filter != "All":
                                safe_user = user_filter.replace("'", "''")
                                conditions.append(f"user_name = '{safe_user}'")
                            
                            where_clause = " AND ".join(conditions)
                            query = f"SELECT * FROM trello_time_tracking WHERE {where_clause} ORDER BY created_at DESC"
                        else:
                            query = "SELECT * FROM trello_time_tracking ORDER BY created_at DESC"
                        
                        if record_limit != "All":
                            query += f" LIMIT {record_limit}"
                        
                        result = conn.execute(text(query))
                        rows = result.fetchall()
                        if rows:
                            df_db = pd.DataFrame([dict(row._mapping) for row in rows])
                        else:
                            df_db = pd.DataFrame()
                except Exception as query_error:
                    st.error(f"Query error: {str(query_error)}")
                    df_db = pd.DataFrame()
                
                if not df_db.empty:
                    st.subheader("Database Records")
                    
                    # Display editable dataframe
                    edited_df = st.data_editor(
                        df_db,
                        use_container_width=True,
                        num_rows="dynamic",
                        column_config={
                            "id": st.column_config.NumberColumn("ID", disabled=True),
                            "created_at": st.column_config.DatetimeColumn("Created At", disabled=True),
                            "card_name": st.column_config.TextColumn("Card Name", max_chars=500),
                            "user_name": st.column_config.TextColumn("User", max_chars=255),
                            "list_name": st.column_config.TextColumn("List", max_chars=255),
                            "time_spent_seconds": st.column_config.NumberColumn("Time Spent (s)", min_value=0),
                            "card_estimate_seconds": st.column_config.NumberColumn("Estimate (s)", min_value=0),
                            "board_name": st.column_config.TextColumn("Board", max_chars=255),
                            "date_started": st.column_config.DateColumn("Date Started"),
                            "labels": st.column_config.TextColumn("Labels")
                        },
                        key="database_editor"
                    )
                    
                    # Action buttons
                    col1, col2, col3, col4 = st.columns(4)
                    
                    with col1:
                        if st.button("💾 Save Changes", type="primary"):
                            try:
                                # Compare edited data with original and update database
                                changes_made = 0
                                
                                # Get the original data again for comparison
                                with engine.connect() as conn:
                                    if card_filter or user_filter != "All":
                                        conditions = []
                                        if card_filter:
                                            safe_filter = card_filter.replace("'", "''")
                                            conditions.append(f"card_name ILIKE '%{safe_filter}%'")
                                        if user_filter != "All":
                                            safe_user = user_filter.replace("'", "''")
                                            conditions.append(f"user_name = '{safe_user}'")
                                        
                                        where_clause = " AND ".join(conditions)
                                        query = f"SELECT * FROM trello_time_tracking WHERE {where_clause} ORDER BY created_at DESC"
                                    else:
                                        query = "SELECT * FROM trello_time_tracking ORDER BY created_at DESC"
                                    
                                    if record_limit != "All":
                                        query += f" LIMIT {record_limit}"
                                    
                                    result = conn.execute(text(query))
                                    original_rows = result.fetchall()
                                    original_df = pd.DataFrame([dict(row._mapping) for row in original_rows]) if original_rows else pd.DataFrame()
                                
                                # Update each changed record
                                for idx in range(len(edited_df)):
                                    if idx < len(original_df):
                                        edited_row = edited_df.iloc[idx]
                                        original_row = original_df.iloc[idx]
                                        
                                        # Check if this row has changes
                                        row_changed = False
                                        for col in ['card_name', 'user_name', 'list_name', 'time_spent_seconds', 'card_estimate_seconds', 'board_name', 'labels']:
                                            if col in edited_row and col in original_row:
                                                if str(edited_row[col]) != str(original_row[col]):
                                                    row_changed = True
                                                    break
                                        
                                        if row_changed:
                                            with engine.connect() as conn:
                                                conn.execute(text('''
                                                    UPDATE trello_time_tracking 
                                                    SET card_name = :card_name,
                                                        user_name = :user_name,
                                                        list_name = :list_name,
                                                        time_spent_seconds = :time_spent_seconds,
                                                        card_estimate_seconds = :card_estimate_seconds,
                                                        board_name = :board_name,
                                                        labels = :labels
                                                    WHERE id = :id
                                                '''), {
                                                    'id': edited_row['id'],
                                                    'card_name': edited_row['card_name'],
                                                    'user_name': edited_row['user_name'],
                                                    'list_name': edited_row['list_name'],
                                                    'time_spent_seconds': edited_row['time_spent_seconds'],
                                                    'card_estimate_seconds': edited_row['card_estimate_seconds'],
                                                    'board_name': edited_row['board_name'],
                                                    'labels': edited_row['labels']
                                                })
                                                conn.commit()
                                                changes_made += 1
                                
                                if changes_made > 0:
                                    st.success(f"Successfully updated {changes_made} records in the database!")
                                    st.rerun()
                                else:
                                    st.info("No changes detected to save.")
                                    
                            except Exception as e:
                                st.error(f"Error saving changes: {str(e)}")
                    
                    with col2:
                        # Add manual deletion by ID
                        delete_id = st.number_input("Enter ID to delete:", min_value=1, step=1, key="delete_id_input")
                        if st.button("🗑️ Delete Record", type="secondary"):
                            if delete_id:
                                try:
                                    with engine.connect() as conn:
                                        result = conn.execute(text("DELETE FROM trello_time_tracking WHERE id = :id"), {"id": delete_id})
                                        conn.commit()
                                        if result.rowcount > 0:
                                            st.success(f"Deleted record with ID {delete_id}")
                                            st.rerun()
                                        else:
                                            st.warning(f"No record found with ID {delete_id}")
                                except Exception as e:
                                    st.error(f"Error deleting record: {str(e)}")
                    
                    with col3:
                        if st.button("🔄 Refresh Data"):
                            st.rerun()
                    
                    with col4:
                        # Download current view as CSV
                        csv_buffer = io.StringIO()
                        df_db.to_csv(csv_buffer, index=False)
                        st.download_button(
                            label="📥 Download CSV",
                            data=csv_buffer.getvalue(),
                            file_name="database_export.csv",
                            mime="text/csv"
                        )
                    
                    # Bulk operations
                    st.subheader("Bulk Operations")
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        if st.button("⚠️ Clear All Data", type="secondary"):
                            if st.checkbox("I understand this will delete ALL data"):
                                try:
                                    with engine.connect() as conn:
                                        conn.execute(text("DELETE FROM trello_time_tracking"))
                                        conn.commit()
                                    st.success("All data cleared successfully!")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error clearing data: {str(e)}")
                    
                    with col2:
                        st.info("💡 Tip: Use filters above to manage specific subsets of data")
                
                else:
                    st.warning("No records found matching the current filters.")
            
            else:
                st.info("Database is empty. Upload CSV data first in the 'Upload & Analyse CSV' tab.")
        
        except Exception as e:
            st.error(f"Error accessing database: {str(e)}")

if __name__ == "__main__":
    main()

import streamlit as st
import pandas as pd
import numpy as np
from datetime import timedelta
from collections import Counter
import io

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
            
            # Calculate completion status
            completion = calculate_completion_status(total_time_spent, estimated_time)
            
            book_summary_data.append({
                'Book Title': book_title,
                'Main User': main_user,
                'Time Spent': format_seconds_to_time(total_time_spent),
                'Estimated Time': format_seconds_to_time(estimated_time),
                'Completion': completion
            })
        
        return pd.DataFrame(book_summary_data)
    
    except Exception as e:
        st.error(f"Error processing book summary: {str(e)}")
        return pd.DataFrame()

def process_user_task_breakdown(df):
    """Generate User Task Breakdown Table with aggregated time"""
    try:
        # Group by User, Book Title (Card name), and List (stage/task)
        # Aggregate time spent for duplicate combinations
        aggregated = df.groupby(['User', 'Card name', 'List'])['Time spent (s)'].sum().reset_index()
        
        # Rename columns for clarity
        aggregated.columns = ['User', 'Book Title', 'List', 'Time Spent (s)']
        
        # Format time spent
        aggregated['Time Spent'] = aggregated['Time Spent (s)'].apply(format_seconds_to_time)
        
        # Drop the seconds column as we now have formatted time
        aggregated = aggregated.drop('Time Spent (s)', axis=1)
        
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
    st.markdown("Upload your Trello CSV export to analyze book production summaries and user task breakdowns.")
    
    # File upload
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
            - `Board name` - Trello board name
            - `Labels` - Any labels associated with the card
            - Any other Trello export columns
            """)

if __name__ == "__main__":
    main()

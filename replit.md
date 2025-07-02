# replit.md

## Overview

This is a Streamlit-based time tracking application focused on book production management. The application provides manual data entry for detailed stage-specific time tracking, completion status visualization, and user activity analysis. It supports comprehensive book production workflows including editorial stages, proofing cycles, and design processes.

## System Architecture

**Frontend Architecture:**
- Single-page Streamlit application (`app.py`)
- Pure Python-based web interface with automatic UI generation
- Real-time data processing and visualization

**Backend Architecture:**
- Serverless architecture using Streamlit's built-in server
- PostgreSQL database for persistent data storage
- In-memory data processing using pandas and numpy
- SQLAlchemy for database operations with duplicate prevention

**Data Processing:**
- Pandas for data manipulation and analysis
- Numpy for numerical computations
- Manual data entry forms for time tracking input

## Key Components

**Core Application (`app.py`):**
- Main Streamlit application entry point
- Data processing and analysis functions
- Time formatting utilities
- Completion status calculation logic

**Key Functions:**
- `format_seconds_to_time()`: Converts seconds to human-readable time format (hh:mm:ss)
- `calculate_completion_status()`: Determines completion percentage based on time spent vs estimated time
- `process_book_summary()`: Generates book-level summary analytics with visual progress tracking
- `get_most_recent_activity()`: Determines the most recent stage/list worked on for each book
- `create_progress_bar_html()`: Creates HTML progress bars for visual completion status

**Data Processing Pipeline:**
1. File upload and data ingestion
2. Data validation and cleaning
3. Grouping and aggregation by book titles
4. User activity analysis
5. Time allocation calculations
6. Completion status determination

## Data Flow

1. **Input**: Manual data entry through web forms
2. **Processing**: 
   - Data validated and stored in PostgreSQL database
   - Grouped by 'Card name' (book titles)
   - Aggregated by user and time spent
   - Completion ratios calculated
3. **Output**: 
   - Book completion progress visualization
   - User activity reports
   - Time allocation analysis

**Data Schema:**
- `card_name`: Book or task identifier
- `time_spent_seconds`: Time spent in seconds
- `user_name`: User identifier
- `list_name`: Production stage identifier
- `board_name`: Project board identifier
- `date_started`: Optional start date
- `created_at`: Entry timestamp

## External Dependencies

**Python Libraries:**
- `streamlit`: Web application framework
- `pandas`: Data manipulation and analysis
- `numpy`: Numerical computing
- `datetime`: Date and time handling
- `collections`: Data structure utilities
- `io`: Input/output operations
- `sqlalchemy`: Database ORM for PostgreSQL operations
- `psycopg2-binary`: PostgreSQL database adapter

**Deployment Requirements:**
- Python 3.7+
- Streamlit runtime environment
- Memory sufficient for data processing (varies by dataset size)

## Deployment Strategy

**Replit Deployment:**
- Single-file Streamlit application
- Automatic dependency management via requirements.txt (if present)
- Web-based interface accessible through Replit's hosting

**Local Development:**
- Run with `streamlit run app.py`
- Hot reload for development
- No database setup required

**Production Considerations:**
- Stateless application design
- File upload size limitations
- Memory usage scales with dataset size
- No persistent data storage

## Changelog

```
Changelog:
- July 01, 2025. Initial setup
- July 01, 2025. Added PostgreSQL database integration with duplicate prevention
- July 01, 2025. Added user filtering interface with date range selection
- July 01, 2025. Implemented tabbed interface for CSV upload and user task filtering
- July 02, 2025. Added separate "Book Completion" tab with visual progress bars, current stage tracking, and search functionality
- July 02, 2025. Added manual data entry form above CSV upload with detailed stage-specific time tracking fields (Editorial R&D, Writing, Proofs 1-5, Sign-offs, Design stages)
- July 02, 2025. Removed Database Management tab and all related code per user request
- July 02, 2025. Removed CSV upload functionality - app now focuses exclusively on manual data entry with stage-specific time tracking
- July 02, 2025. Enhanced Book Completion tab with expandable dropdowns, individual task timers, and aggregated time tracking per user/stage combination
```

## User Preferences

```
Preferred communication style: Simple, everyday language.
```

## Additional Notes

**Architecture Decisions:**
- **Streamlit Choice**: Chosen for rapid prototyping and simple deployment, eliminating need for separate frontend/backend
- **In-Memory Processing**: Suitable for typical file sizes, avoiding database complexity
- **Function-Based Design**: Modular approach for easy testing and maintenance
- **Pandas/Numpy Stack**: Standard Python data science tools for robust data manipulation

**Limitations:**
- No persistent data storage
- Single-user session model
- File size constraints based on available memory
- No real-time data synchronization

**Future Enhancements:**
- Database integration for persistent storage
- Multi-user authentication
- Real-time data updates
- Advanced visualization capabilities
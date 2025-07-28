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
- Requires PostgreSQL connection via the `DATABASE_URL` environment variable or a
  `.streamlit/secrets.toml` file.

### Database Configuration
Set the `DATABASE_URL` environment variable to a full PostgreSQL connection
string, for example:

```
postgresql://user:password@host:port/database
```

Alternatively create a `.streamlit/secrets.toml` file containing:

```
[database]
url = "postgresql://user:password@host:port/database"
```

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
- July 02, 2025. Added Archive tab with archiving/unarchiving functionality and delete capability with confirmation
- July 02, 2025. Updated manual entry form headings: Editorial R&D, Editorial Writing, 1st Edit, 2nd Edit, Design R&D, In Design, 1st Proof, 2nd Proof, Editorial Sign Off, Design Sign Off
- July 02, 2025. Reorganized design categories: Design R&D, In Design, Design Sign Off allocated to Design team; remaining stages to Editorial team
- July 02, 2025. Implemented tab state persistence using dropdown selector to prevent jumping back to first tab on button clicks
- July 02, 2025. Removed emojis from archive functionality and added delete button with double-click confirmation
- July 02, 2025. Reorganized and renamed tabs: Book Progress (formerly Book Completion), Add Book (formerly Data Entry), Archive, User Data (formerly Filter User Tasks)
- July 02, 2025. Added session tracking: records date and time when timer sessions start, displays in User Data tab as "Session Started" column with DD/MM/YYYY HH:MM format
- July 02, 2025. Fixed alphabetical sorting: books remain in alphabetical order when timer sessions end, preventing list reordering and auto-collapse
- July 02, 2025. Added "Time Allocation" column to User Data table showing estimated time for each task alongside actual time spent
- July 02, 2025. Enhanced manual entry form with dual functionality: task assignment (estimates) and manual time recording (actual completed work with specific dates/times)
- July 02, 2025. Fixed Book Progress display: updated stage names to match actual form fields (1st Edit, 2nd Edit, Design R&D, In Design) and corrected default estimates
- July 02, 2025. Implemented UTC+1 timezone for all date/time operations including timers, manual entries, and session tracking
- July 02, 2025. Removed circular scroll-to-top button due to compatibility issues with Streamlit
- July 02, 2025. Removed Manual Time Recording section from Add Book tab as requested
- July 02, 2025. Fixed form clearing: implemented explicit field clearing with default values after successful book creation
- July 02, 2025. Fixed session state modification error by using form clearing flag instead of direct modification
- July 02, 2025. Moved success message from top to bottom of Add Book form (below the Add Entry button)
- July 02, 2025. Implemented comprehensive form clearing: all form field keys removed from session state after book creation to ensure complete reset
- July 02, 2025. Improved tab switching responsiveness by adding immediate rerun when tab selection changes
- July 02, 2025. Added delete button to Book Progress tab alongside archive button with double-click confirmation for permanent deletion
- July 02, 2025. Fixed time estimation display in Book Progress - individual task estimates now show correct values from database instead of defaulting to 1 hour
- July 02, 2025. Improved estimate retrieval logic to handle multiple database records per user/stage by prioritizing records with actual estimate values
- July 02, 2025. Updated Task Assignment form: replaced "None" with "Not set" and enabled time estimates without user assignment
- July 02, 2025. Added user reassignment functionality to Book Progress tab with stage-appropriate user dropdown options
- July 03, 2025. Implemented persistent timer functionality: timers now survive page refreshes and tab closures by storing state in database
- July 03, 2025. Added real-time JavaScript timer displays that update every second showing live elapsed time without requiring page refreshes
- July 07, 2025. Enhanced dropdown titles to show "Stage Name | User | Time/Estimate" format when collapsed for better progress visibility
- July 07, 2025. Added bold book titles with progress percentages on main cards (e.g., "**Test Book** (41.8%)" or "**Book Name** (20% over estimate)")
- July 07, 2025. Implemented comprehensive tag system: dropdown in Add Book form, database storage, and display in Book Progress cards
- July 07, 2025. Renamed "Filter User Tasks" to "Reporting" with enhanced filtering by User, Book, Board, Tag, and date range
- July 11, 2025. Added completion checkbox functionality: each task dropdown now includes a "Completed" checkbox that adds ✅ tick emoji to dropdown titles when marked as completed
- July 11, 2025. Added book-level completion indicator: main book card shows ✅ tick emoji when all tasks are marked as completed
- July 11, 2025. Added "Remove stage" functionality: each task now has a button positioned in the right column for single-click deletion of individual stages
- July 11, 2025. Enhanced book management: books can now be created without tasks and remain visible even when all tasks are deleted
- July 11, 2025. Added books table: separate tracking for books independent of tasks, allowing books to exist without any assigned stages
- July 11, 2025. Added "Add stage" dropdown: each book card now includes a dropdown to add missing stages, showing only stages not currently associated with the book
- July 11, 2025. Enhanced "Add stage" with time input: added hours field next to dropdown allowing custom time estimates when adding stages
- July 11, 2025. Enhanced Book search filter in Reporting with type-to-search functionality while keeping Board and Tag as simple dropdowns
- July 11, 2025. Fixed Board field to display actual form values instead of defaulting to "Manual Entry"
- July 11, 2025. Updated Reporting filtered results to show all books and stages regardless of time spent for complete project visibility
- July 11, 2025. Added completion percentage column to Reporting filtered results showing task progress relative to time estimates
- July 11, 2025. Added pagination to Book Progress tab (10 books per page) with navigation controls to improve performance with large datasets
- July 11, 2025. Enhanced tab switching with automatic refresh and added loading spinners for better user experience
- July 23, 2025. Fixed completion emoji issue on book cards: completion status now updates immediately when checkboxes are changed
- July 23, 2025. Added comprehensive database retry logic to handle PostgreSQL SSL connection errors
- July 23, 2025. Added maximum time validation (100 hours) for manual time entry to prevent database integer overflow errors
- July 23, 2025. Optimized refresh system by removing unnecessary st.rerun() calls from timer operations, manual time additions, and completion status changes - only major structural changes now trigger full page refreshes
- July 23, 2025. Fixed search functionality for book titles containing special characters (parentheses, etc.) by adding regex escaping
- July 23, 2025. Added basic login system with password protection (password: Booklife01) - users must authenticate before accessing the application
- July 23, 2025. Enhanced login system with IP-based 24-hour authentication persistence - users don't need to re-login for 24 hours from the same device/IP address, includes "Full Logout" option to clear IP authentication
- July 23, 2025. Removed logout buttons from interface per user request - authentication persists for full 24-hour period automatically
- July 23, 2025. Enhanced timer persistence system with comprehensive database storage: added accumulated_seconds and is_paused columns to active_timers table, timers now fully preserve pause/resume states and accumulated time across page refreshes, added continuous database sync to prevent timer resets, implemented background timer state updates to keep database current
- July 23, 2025. Fixed critical multi-user timer race condition causing exponential time growth: removed problematic background update function that was accumulating time incorrectly when multiple users accessed the app, improved timer loading logic to properly handle accumulated time vs current session time, fixed pause/resume calculations to prevent time duplication
- July 23, 2025. Fixed unique constraint violation error when stopping timers: implemented ON CONFLICT DO UPDATE logic to handle duplicate entries that occur when timers record identical time durations on the same date, preventing database errors during timer stops
- July 23, 2025. Fixed timer display showing 00:00:00: corrected timer loading logic to properly calculate accumulated time for running timers restored from database
- July 23, 2025. Enhanced timer stop robustness: improved error handling to clear timer states immediately on stop button click, preventing need for double-clicks and ensuring proper cleanup even when database errors occur
- July 23, 2025. Fixed critical KeyError in timer operations: added proper key existence checks using .get() method to prevent crashes when accessing timer states that don't exist due to user reassignments or stage changes
- July 23, 2025. Fixed timer stop requiring double-click to show updated totals: added major_update_needed flag to trigger immediate display refresh after successful timer stop, ensuring time totals update on first click
- July 23, 2025. Replaced Show All button with overview table: removed Show All/Hide All toggle functionality and added "All Books Overview" table below book cards displaying Book Name and Board columns for complete project visibility
- July 24, 2025. Simplified timer system to eliminate background compute usage: removed complex pause/resume functionality, JavaScript timers, and continuous database synchronization - timers now calculate elapsed time on-demand using simple start_time to current_time calculation, significantly reducing compute unit consumption
- July 24, 2025. Removed active timers display section: eliminated the "Active Timers" section at the top of Book Progress tab to further reduce any potential background compute usage while maintaining timer functionality within individual book cards
- July 24, 2025. Restored simplified active timers display: brought back "Active Timers" section showing only book names without time displays, refresh buttons, or any background processing to minimize compute usage while providing visibility into which books have running timers
- July 24, 2025. Fixed timezone calculation issues causing negative timer displays: standardized all timer calculations to use UTC as base time with proper BST conversion, ensuring accurate time calculations for UK users regardless of daylight saving time transitions
- July 24, 2025. CRITICAL FIX: Eliminated infinite rerun loop causing excessive compute usage (1M+ units/day): removed automatic refresh system that was triggering st.rerun() repeatedly when major_update_needed flags were set, significantly reducing compute unit consumption to normal levels
- July 24, 2025. Updated login password from "testpassword1" to "Booklife01" for improved security and branding alignment
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
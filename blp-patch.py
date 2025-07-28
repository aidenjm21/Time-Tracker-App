diff --git a/.gitignore b/.gitignore
new file mode 100644
index 0000000000000000000000000000000000000000..fe8f91ed71a10d9c47c69eec5d21cc6fe8baba5d
--- /dev/null
+++ b/.gitignore
@@ -0,0 +1,5 @@
+__pycache__/
+*.pyc
+password.json
+.streamlit/secrets.toml
+.env
diff --git a/.streamlit/config.toml b/.streamlit/config.toml
index 35923c30dd88275bf6e801939fcbf369b85aacca..f257554ba68fbcb55336430de5177c25732a6a31 100644
--- a/.streamlit/config.toml
+++ b/.streamlit/config.toml
@@ -1,4 +1,4 @@
 [server]
 headless = true
 address = "0.0.0.0"
-port = 5000
+port = 8501
diff --git a/app.py b/app.py
index c88c42cc87f18871133ef1e16853d9a988c6d0df..e2cd7c4274a17e9ba36ba0f97dfc44913b5f0f03 100644
--- a/app.py
+++ b/app.py
@@ -1,26 +1,2498 @@
-nt = conn.execute(text('SELECT COUNT(*) FROM trello_time_tracking WHERE archived = TRUE')).scalar()
+import streamlit as st
+import pandas as pd
+import numpy as np
+from datetime import datetime, timedelta, timezone
+from collections import Counter
+import io
+import os
+import re
+import time
+from sqlalchemy import create_engine, text
+from sqlalchemy.exc import IntegrityError
+
+# Set BST timezone (UTC+1)
+BST = timezone(timedelta(hours=1))
+UTC_PLUS_1 = BST  # Keep backward compatibility
+
+# -------- Database Initialisation ---------
+
+@st.cache_resource
+def init_database():
+    """Initialise database connection and create tables"""
+    try:
+        # Prefer Streamlit secrets but allow an env var fallback
+        database_url = st.secrets.get("database", {}).get("url") or os.getenv("DATABASE_URL")
+        if not database_url:
+            st.error(
+                "Database URL not configured. Set database.url in Streamlit secrets "
+                "or the DATABASE_URL environment variable."
+            )
+            return None
+        
+        engine = create_engine(database_url)
+        
+        # Create table if it doesn't exist
+        with engine.connect() as conn:
+            conn.execute(text('''
+                CREATE TABLE IF NOT EXISTS trello_time_tracking (
+                    id SERIAL PRIMARY KEY,
+                    card_name VARCHAR(500) NOT NULL,
+                    user_name VARCHAR(255) NOT NULL,
+                    list_name VARCHAR(255) NOT NULL,
+                    time_spent_seconds INTEGER NOT NULL,
+                    date_started DATE,
+                    card_estimate_seconds INTEGER,
+                    board_name VARCHAR(255),
+                    labels TEXT,
+                    archived BOOLEAN DEFAULT FALSE,
+                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
+                    UNIQUE(card_name, user_name, list_name, date_started, time_spent_seconds)
+                )
+            '''))
+            # Add archived column to existing table if it doesn't exist
+            conn.execute(text('''
+                ALTER TABLE trello_time_tracking 
+                ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE
+            '''))
+            
+            # Add session_start_time column if it doesn't exist
+            conn.execute(text('''
+                ALTER TABLE trello_time_tracking 
+                ADD COLUMN IF NOT EXISTS session_start_time TIMESTAMP
+            '''))
+            
+            # Add tag column if it doesn't exist
+            conn.execute(text('''
+                ALTER TABLE trello_time_tracking 
+                ADD COLUMN IF NOT EXISTS tag VARCHAR(255)
+            '''))
+            
+            # Create active timers table for persistent timer storage
+            conn.execute(text('''
+                CREATE TABLE IF NOT EXISTS active_timers (
+                    id SERIAL PRIMARY KEY,
+                    timer_key VARCHAR(500) NOT NULL UNIQUE,
+                    card_name VARCHAR(255) NOT NULL,
+                    user_name VARCHAR(100),
+                    list_name VARCHAR(100) NOT NULL,
+                    board_name VARCHAR(100),
+                    start_time TIMESTAMPTZ NOT NULL,
+                    accumulated_seconds INTEGER DEFAULT 0,
+                    is_paused BOOLEAN DEFAULT FALSE,
+                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
+                )
+            '''))
+            
+            # Add new columns to existing active_timers table if they don't exist
+            conn.execute(text('''
+                ALTER TABLE active_timers 
+                ADD COLUMN IF NOT EXISTS accumulated_seconds INTEGER DEFAULT 0
+            '''))
+            conn.execute(text('''
+                ALTER TABLE active_timers 
+                ADD COLUMN IF NOT EXISTS is_paused BOOLEAN DEFAULT FALSE
+            '''))
+            
+            # Migrate existing TIMESTAMP columns to TIMESTAMPTZ if needed
+            try:
+                conn.execute(text('''
+                    ALTER TABLE active_timers 
+                    ALTER COLUMN start_time TYPE TIMESTAMPTZ USING start_time AT TIME ZONE 'Europe/London'
+                '''))
+                conn.execute(text('''
+                    ALTER TABLE active_timers 
+                    ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'Europe/London'
+                '''))
+            except Exception:
+                # Columns might already be TIMESTAMPTZ, ignore the error
+                pass
+            conn.commit()
+        
+        return engine
+    except Exception as e:
+        st.error(f"Database initialisation failed: {str(e)}")
+        return None
+
+
+
+
+def get_users_from_database(_engine):
+    """Get list of unique users from database with retry logic"""
+    max_retries = 3
+    for attempt in range(max_retries):
+        try:
+            with _engine.connect() as conn:
+                result = conn.execute(text('SELECT DISTINCT COALESCE(user_name, \'Not set\') FROM trello_time_tracking ORDER BY COALESCE(user_name, \'Not set\')'))
+                return [row[0] for row in result]
+        except Exception as e:
+            if attempt < max_retries - 1:
+                time.sleep(0.5)
+                continue
+            else:
+                return []
+    return []
+
+def get_tags_from_database(_engine):
+    """Get list of unique individual tags from database, splitting comma-separated values"""
+    max_retries = 3
+    for attempt in range(max_retries):
+        try:
+            with _engine.connect() as conn:
+                result = conn.execute(text("SELECT DISTINCT tag FROM trello_time_tracking WHERE tag IS NOT NULL AND tag != '' ORDER BY tag"))
+                all_tag_strings = [row[0] for row in result]
+                
+                # Split comma-separated tags and create unique set
+                individual_tags = set()
+                for tag_string in all_tag_strings:
+                    if tag_string:
+                        # Split by comma and strip whitespace
+                        tags_in_string = [tag.strip() for tag in tag_string.split(',')]
+                        individual_tags.update(tags_in_string)
+                
+                # Return sorted list of individual tags
+                return sorted(list(individual_tags))
+                
+        except Exception as e:
+            if attempt < max_retries - 1:
+                # Wait before retrying
+                time.sleep(0.5)
+                continue
+            else:
+                # Final attempt failed, return empty list instead of showing error
+                return []
+    
+    return []
+
+def get_books_from_database(_engine):
+    """Get list of unique book names from database with retry logic"""
+    max_retries = 3
+    for attempt in range(max_retries):
+        try:
+            with _engine.connect() as conn:
+                result = conn.execute(text("SELECT DISTINCT card_name FROM trello_time_tracking WHERE card_name IS NOT NULL ORDER BY card_name"))
+                books = [row[0] for row in result]
+                return books
+        except Exception as e:
+            if attempt < max_retries - 1:
+                time.sleep(0.5)
+                continue
+            else:
+                return []
+    return []
+
+def get_boards_from_database(_engine):
+    """Get list of unique board names from database with retry logic"""
+    max_retries = 3
+    for attempt in range(max_retries):
+        try:
+            with _engine.connect() as conn:
+                result = conn.execute(text("SELECT DISTINCT board_name FROM trello_time_tracking WHERE board_name IS NOT NULL AND board_name != '' ORDER BY board_name"))
+                boards = [row[0] for row in result]
+                return boards
+        except Exception as e:
+            if attempt < max_retries - 1:
+                time.sleep(0.5)
+                continue
+            else:
+                return []
+    return []
+
+
+def emergency_stop_all_timers(engine):
+    """Emergency function to stop all active timers and save progress when database connection fails"""
+    try:
+        # Initialize session state if needed
+        if 'timers' not in st.session_state:
+            st.session_state.timers = {}
+        if 'timer_start_times' not in st.session_state:
+            st.session_state.timer_start_times = {}
+        
+        saved_timers = 0
+        current_time_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
+        current_time_bst = current_time_utc.astimezone(BST)
+        
+        # Process any active timers from session state
+        for timer_key, is_active in st.session_state.timers.items():
+            if is_active and timer_key in st.session_state.timer_start_times:
+                try:
+                    # Parse timer key to extract details
+                    parts = timer_key.split('_')
+                    if len(parts) >= 3:
+                        card_name = '_'.join(parts[:-2])  # Reconstruct card name
+                        list_name = parts[-2]
+                        user_name = parts[-1]
+                        
+                        # Calculate elapsed time using UTC-based function
+                        start_time = st.session_state.timer_start_times[timer_key]
+                        elapsed_seconds = calculate_timer_elapsed_time(start_time)
+                        
+                        # Only save if significant time elapsed
+                        if elapsed_seconds > 0:
+                            # Try to save to database with retry logic
+                            for attempt in range(3):
+                                try:
+                                    with engine.connect() as conn:
+                                        # Save the time entry
+                                        conn.execute(text('''
+                                            INSERT INTO trello_time_tracking 
+                                            (card_name, user_name, list_name, time_spent_seconds, 
+                                             date_started, session_start_time, board_name)
+                                            VALUES (:card_name, :user_name, :list_name, :time_spent_seconds, 
+                                                   :date_started, :session_start_time, :board_name)
+                                        '''), {
+                                            'card_name': card_name,
+                                            'user_name': user_name,
+                                            'list_name': list_name,
+                                            'time_spent_seconds': elapsed_seconds,
+                                            'date_started': start_time.date(),
+                                            'session_start_time': start_time,
+                                            'board_name': 'Manual Entry'
+                                        })
+                                        
+                                        # Remove from active timers table
+                                        conn.execute(text('DELETE FROM active_timers WHERE timer_key = :timer_key'), 
+                                                   {'timer_key': timer_key})
+                                        conn.commit()
+                                        saved_timers += 1
+                                        break
+                                except Exception:
+                                    if attempt == 2:  # Last attempt failed
+                                        # Store in session state as backup
+                                        if 'emergency_saved_times' not in st.session_state:
+                                            st.session_state.emergency_saved_times = []
+                                        st.session_state.emergency_saved_times.append({
+                                            'card_name': card_name,
+                                            'user_name': user_name,
+                                            'list_name': list_name,
+                                            'elapsed_seconds': elapsed_seconds,
+                                            'start_time': start_time
+                                        })
+                                    continue
+                
+                except Exception as e:
+                    continue  # Skip this timer if parsing fails
+        
+        if saved_timers > 0:
+            st.success(f"Successfully saved {saved_timers} active timer(s) before stopping.")
+        
+        # Try to clear active timers table if possible
+        try:
+            with engine.connect() as conn:
+                conn.execute(text('DELETE FROM active_timers'))
+                conn.commit()
+        except Exception:
+            pass  # Database might be completely unavailable
+            
+    except Exception as e:
+        st.error(f"Emergency timer save failed: {str(e)}")
+
+
+def recover_emergency_saved_times(engine):
+    """Recover and save any emergency saved times from previous session"""
+    if 'emergency_saved_times' in st.session_state and st.session_state.emergency_saved_times:
+        saved_count = 0
+        for saved_time in st.session_state.emergency_saved_times:
+            try:
+                with engine.connect() as conn:
+                    conn.execute(text('''
+                        INSERT INTO trello_time_tracking 
+                        (card_name, user_name, list_name, time_spent_seconds, 
+                         date_started, session_start_time, board_name)
+                        VALUES (:card_name, :user_name, :list_name, :time_spent_seconds, 
+                               :date_started, :session_start_time, :board_name)
+                    '''), {
+                        'card_name': saved_time['card_name'],
+                        'user_name': saved_time['user_name'],
+                        'list_name': saved_time['list_name'],
+                        'time_spent_seconds': saved_time['elapsed_seconds'],
+                        'date_started': saved_time['start_time'].date(),
+                        'session_start_time': saved_time['start_time'],
+                        'board_name': 'Manual Entry'
+                    })
+                    conn.commit()
+                    saved_count += 1
+            except Exception:
+                continue  # Skip if unable to save
+        
+        if saved_count > 0:
+            st.success(f"Recovered {saved_count} emergency saved timer(s) from previous session.")
+        
+        # Clear the emergency saved times
+        st.session_state.emergency_saved_times = []
+
+
+def load_active_timers(engine):
+    """Load active timers from database - simplified version"""
+    try:
+        with engine.connect() as conn:
+            result = conn.execute(text('''
+                SELECT timer_key, card_name, user_name, list_name, board_name, start_time
+                FROM active_timers
+                ORDER BY start_time DESC
+            '''))
+            
+            active_timers = []
+            for row in result:
+                timer_key = row[0]
+                card_name = row[1]
+                user_name = row[2]
+                list_name = row[3]
+                board_name = row[4]
+                start_time = row[5]
+                
+                # Simple session state - just track if timer is running
+                if 'timers' not in st.session_state:
+                    st.session_state.timers = {}
+                if 'timer_start_times' not in st.session_state:
+                    st.session_state.timer_start_times = {}
+                
+                # Ensure timezone-aware datetime for consistency
+                if start_time.tzinfo is None:
+                    start_time_with_tz = start_time.replace(tzinfo=BST)
+                else:
+                    # Convert to BST for consistency in session state
+                    start_time_with_tz = start_time.astimezone(BST)
+                
+                st.session_state.timers[timer_key] = True
+                st.session_state.timer_start_times[timer_key] = start_time_with_tz
+                
+                active_timers.append({
+                    'timer_key': timer_key,
+                    'card_name': card_name,
+                    'user_name': user_name,
+                    'list_name': list_name,
+                    'board_name': board_name,
+                    'start_time': start_time_with_tz
+                })
+            
+            return active_timers
+    except Exception as e:
+        error_msg = str(e)
+        
+        # Check if this is an SSL connection error indicating app restart
+        if "SSL connection has been closed unexpectedly" in error_msg or "connection" in error_msg.lower():
+            st.warning("App restarted - automatically stopping all active timers and saving progress...")
+            
+            # Try to recover and save any active timers from session state
+            emergency_stop_all_timers(engine)
+            
+            # Clear session state timers since they've been saved
+            if 'timers' in st.session_state:
+                st.session_state.timers = {}
+            if 'timer_start_times' in st.session_state:
+                st.session_state.timer_start_times = {}
+                
+            return []
+        else:
+            st.error(f"Error loading active timers: {error_msg}")
+            return []
+
+
+def save_active_timer(engine, timer_key, card_name, user_name, list_name, board_name, start_time):
+    """Save active timer to database - timezone-aware version"""
+    try:
+        with engine.connect() as conn:
+            # Ensure timezone information is preserved for database storage
+            if start_time.tzinfo is None:
+                # If no timezone, assume BST
+                start_time_with_tz = start_time.replace(tzinfo=BST)
+            else:
+                # Keep existing timezone info
+                start_time_with_tz = start_time
+                
+            conn.execute(text('''
+                INSERT INTO active_timers (timer_key, card_name, user_name, list_name, board_name, start_time, created_at)
+                VALUES (:timer_key, :card_name, :user_name, :list_name, :board_name, :start_time, CURRENT_TIMESTAMP)
+                ON CONFLICT (timer_key) DO UPDATE SET
+                    start_time = EXCLUDED.start_time,
+                    created_at = CURRENT_TIMESTAMP
+            '''), {
+                'timer_key': timer_key,
+                'card_name': card_name,
+                'user_name': user_name,
+                'list_name': list_name,
+                'board_name': board_name,
+                'start_time': start_time_with_tz
+            })
+            conn.commit()
+    except Exception as e:
+        st.error(f"Error saving active timer: {str(e)}")
+
+
+
+
+def remove_active_timer(engine, timer_key):
+    """Remove active timer from database"""
+    try:
+        with engine.connect() as conn:
+            conn.execute(text('''
+                DELETE FROM active_timers WHERE timer_key = :timer_key
+            '''), {'timer_key': timer_key})
+            conn.commit()
+    except Exception as e:
+        st.error(f"Error removing active timer: {str(e)}")
+
+
+def update_task_completion(engine, card_name, user_name, list_name, completed):
+    """Update task completion status for all matching records"""
+    try:
+        with engine.connect() as conn:
+            # Update all matching records and get count of affected rows
+            result = conn.execute(text("""
+                UPDATE trello_time_tracking 
+                SET completed = :completed
+                WHERE card_name = :card_name 
+                AND COALESCE(user_name, 'Not set') = :user_name 
+                AND list_name = :list_name
+                AND archived = FALSE
+            """), {
+                'completed': completed,
+                'card_name': card_name,
+                'user_name': user_name,
+                'list_name': list_name
+            })
+            conn.commit()
+            
+            # Verify the update worked
+            rows_affected = result.rowcount
+            if rows_affected == 0:
+                st.warning(f"No records found to update for {card_name} - {list_name} ({user_name})")
+                
+    except Exception as e:
+        st.error(f"Error updating task completion: {str(e)}")
+
+
+def get_task_completion(engine, card_name, user_name, list_name):
+    """Get task completion status"""
+    try:
+        with engine.connect() as conn:
+            result = conn.execute(text("""
+                SELECT completed FROM trello_time_tracking 
+                WHERE card_name = :card_name 
+                AND COALESCE(user_name, 'Not set') = :user_name 
+                AND list_name = :list_name
+                LIMIT 1
+            """), {
+                'card_name': card_name,
+                'user_name': user_name,
+                'list_name': list_name
+            })
+            row = result.fetchone()
+            return row[0] if row else False
+    except Exception as e:
+        st.error(f"Error getting task completion: {str(e)}")
+        return False
+
+
+def check_all_tasks_completed(engine, card_name):
+    """Check if all tasks for a book are completed"""
+    try:
+        with engine.connect() as conn:
+            # Get all tasks for this book - need to check each user/stage combination
+            result = conn.execute(text("""
+                SELECT list_name, COALESCE(user_name, 'Not set') as user_name, 
+                       BOOL_AND(COALESCE(completed, false)) as all_completed
+                FROM trello_time_tracking 
+                WHERE card_name = :card_name 
+                AND archived = FALSE
+                GROUP BY list_name, COALESCE(user_name, 'Not set')
+            """), {
+                'card_name': card_name
+            })
+            
+            task_groups = result.fetchall()
+            if not task_groups:
+                return False
+            
+            # Check if all task groups are completed
+            for task_group in task_groups:
+                if not task_group[2]:  # all_completed column
+                    return False
+            
+            return True
+    except Exception as e:
+        st.error(f"Error checking book completion: {str(e)}")
+        return False
+
+
+def delete_task_stage(engine, card_name, user_name, list_name):
+    """Delete a specific task stage from the database"""
+    try:
+        with engine.connect() as conn:
+            conn.execute(text("""
+                DELETE FROM trello_time_tracking 
+                WHERE card_name = :card_name 
+                AND COALESCE(user_name, 'Not set') = :user_name 
+                AND list_name = :list_name
+            """), {
+                'card_name': card_name,
+                'user_name': user_name,
+                'list_name': list_name
+            })
+            conn.commit()
+            return True
+    except Exception as e:
+        st.error(f"Error deleting task stage: {str(e)}")
+        return False
+
+
+def create_book_record(engine, card_name, board_name=None, tag=None):
+    """Create a book record in the books table"""
+    try:
+        with engine.connect() as conn:
+            conn.execute(text("""
+                INSERT INTO books (card_name, board_name, tag)
+                VALUES (:card_name, :board_name, :tag)
+                ON CONFLICT (card_name) DO UPDATE SET
+                    board_name = EXCLUDED.board_name,
+                    tag = EXCLUDED.tag
+            """), {
+                'card_name': card_name,
+                'board_name': board_name,
+                'tag': tag
+            })
+            conn.commit()
+            return True
+    except Exception as e:
+        st.error(f"Error creating book record: {str(e)}")
+        return False
+
+
+def get_all_books(engine):
+    """Get all books from the books table, including those without tasks"""
+    try:
+        with engine.connect() as conn:
+            result = conn.execute(text("""
+                SELECT DISTINCT card_name, board_name, tag
+                FROM books
+                WHERE archived = FALSE
+                UNION
+                SELECT DISTINCT card_name, board_name, tag
+                FROM trello_time_tracking
+                WHERE archived = FALSE
+                ORDER BY card_name
+            """))
+            return result.fetchall()
+    except Exception as e:
+        st.error(f"Error fetching books: {str(e)}")
+        return []
+
+
+def get_available_stages_for_book(engine, card_name):
+    """Get stages not yet associated with a book"""
+    all_stages = [
+        "Editorial R&D", "Editorial Writing", "1st Edit", "2nd Edit",
+        "Design R&D", "In Design", "1st Proof", "2nd Proof", 
+        "Editorial Sign Off", "Design Sign Off"
+    ]
+    
+    try:
+        with engine.connect() as conn:
+            result = conn.execute(text("""
+                SELECT DISTINCT list_name
+                FROM trello_time_tracking
+                WHERE card_name = :card_name AND archived = FALSE
+            """), {'card_name': card_name})
+            
+            existing_stages = [row[0] for row in result.fetchall()]
+            available_stages = [stage for stage in all_stages if stage not in existing_stages]
+            return available_stages
+    except Exception as e:
+        st.error(f"Error getting available stages: {str(e)}")
+        return []
+
+
+def add_stage_to_book(engine, card_name, stage_name, board_name=None, tag=None, estimate_seconds=3600):
+    """Add a new stage to a book"""
+    try:
+        with engine.connect() as conn:
+            conn.execute(text("""
+                INSERT INTO trello_time_tracking 
+                (card_name, user_name, list_name, time_spent_seconds, card_estimate_seconds, board_name, created_at, tag)
+                VALUES (:card_name, :user_name, :list_name, :time_spent_seconds, :card_estimate_seconds, :board_name, :created_at, :tag)
+            """), {
+                'card_name': card_name,
+                'user_name': None,  # Unassigned initially
+                'list_name': stage_name,
+                'time_spent_seconds': 0,
+                'card_estimate_seconds': estimate_seconds,
+                'board_name': board_name,
+                'created_at': datetime.now(BST),
+                'tag': tag
+            })
+            conn.commit()
+            return True
+    except Exception as e:
+        st.error(f"Error adding stage: {str(e)}")
+        return False
+
+
+def get_filtered_tasks_from_database(_engine, user_name=None, book_name=None, board_name=None, tag_name=None, start_date=None, end_date=None):
+    """Get filtered tasks from database with multiple filter options"""
+    try:
+        query = '''
+            WITH task_summary AS (
+                SELECT card_name, list_name, COALESCE(user_name, 'Not set') as user_name, board_name, tag,
+                       SUM(time_spent_seconds) as total_time,
+                       MAX(card_estimate_seconds) as estimated_seconds,
+                       MIN(CASE WHEN session_start_time IS NOT NULL THEN session_start_time END) as first_session
+                FROM trello_time_tracking 
+                WHERE 1=1
+        '''
+        params = {}
+        
+        # Add filters based on provided parameters
+        if user_name and user_name != "All Users":
+            query += ' AND COALESCE(user_name, \'Not set\') = :user_name'
+            params['user_name'] = user_name
+            
+        if book_name and book_name != "All Books":
+            query += ' AND card_name = :book_name'
+            params['book_name'] = book_name
+            
+        if board_name and board_name != "All Boards":
+            query += ' AND board_name = :board_name'
+            params['board_name'] = board_name
+            
+        if tag_name and tag_name != "All Tags":
+            query += ' AND (tag = :tag_name OR tag LIKE :tag_name_pattern1 OR tag LIKE :tag_name_pattern2 OR tag LIKE :tag_name_pattern3)'
+            params['tag_name'] = tag_name
+            params['tag_name_pattern1'] = f'{tag_name},%'  # Tag at start
+            params['tag_name_pattern2'] = f'%, {tag_name},%'  # Tag in middle  
+            params['tag_name_pattern3'] = f'%, {tag_name}'  # Tag at end
+        
+        query += '''
+                GROUP BY card_name, list_name, COALESCE(user_name, 'Not set'), board_name, tag
+            )
+            SELECT card_name, list_name, user_name, board_name, tag, first_session, total_time, estimated_seconds
+            FROM task_summary
+        '''
+        
+        # Add date filtering to the main query if needed
+        if start_date or end_date:
+            date_conditions = []
+            if start_date:
+                date_conditions.append('first_session >= :start_date')
+                params['start_date'] = start_date
+            if end_date:
+                date_conditions.append('first_session <= :end_date')
+                params['end_date'] = end_date
+            
+            if date_conditions:
+                query += ' WHERE ' + ' AND '.join(date_conditions)
+        
+        query += ' ORDER BY first_session DESC, card_name, list_name'
+        
+        with _engine.connect() as conn:
+            result = conn.execute(text(query), params)
+            data = []
+            for row in result:
+                card_name = row[0]
+                list_name = row[1]
+                user_name = row[2]
+                board_name = row[3]
+                tag = row[4]
+                first_session = row[5]
+                total_time = row[6]
+                estimated_time = row[7] if row[7] else 0
+                
+                if first_session:
+                    # Format as DD/MM/YYYY HH:MM
+                    date_time_str = first_session.strftime('%d/%m/%Y %H:%M')
+                else:
+                    date_time_str = 'Manual Entry'
+                    
+                # Calculate completion percentage
+                if estimated_time > 0:
+                    completion_ratio = total_time / estimated_time
+                    if completion_ratio <= 1.0:
+                        completion_percentage = f"{int(completion_ratio * 100)}%"
+                    else:
+                        over_percentage = int((completion_ratio - 1.0) * 100)
+                        completion_percentage = f"{over_percentage}% over"
+                else:
+                    completion_percentage = "No estimate"
+                
+                data.append({
+                    'Book Title': card_name,
+                    'Stage': list_name,
+                    'User': user_name,
+                    'Board': board_name,
+                    'Tag': tag if tag else 'No Tag',
+                    'Session Started': date_time_str,
+                    'Time Allocation': format_seconds_to_time(estimated_time) if estimated_time > 0 else 'Not Set',
+                    'Time Spent': format_seconds_to_time(total_time),
+                    'Completion %': completion_percentage
+                })
+            return pd.DataFrame(data)
+    except Exception as e:
+        st.error(f"Error fetching user tasks: {str(e)}")
+        return pd.DataFrame()
+
+def format_seconds_to_time(seconds):
+    """Convert seconds to hh:mm:ss format"""
+    if pd.isna(seconds) or seconds == 0:
+        return "00:00:00"
+    
+    # Convert to integer to handle any float values
+    seconds = int(seconds)
+    hours = seconds // 3600
+    minutes = (seconds % 3600) // 60
+    secs = seconds % 60
+    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
+
+def calculate_timer_elapsed_time(start_time):
+    """Calculate elapsed time from start_time to now using UTC for accuracy"""
+    if not start_time:
+        return 0
+    
+    # Use UTC for all calculations to avoid timezone issues
+    current_time_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
+    
+    # Convert start_time to UTC
+    if start_time.tzinfo is None:
+        # Assume start_time is in BST if no timezone info
+        start_time = start_time.replace(tzinfo=BST).astimezone(timezone.utc)
+    else:
+        # Convert to UTC
+        start_time = start_time.astimezone(timezone.utc)
+    
+    elapsed = current_time_utc - start_time
+    return max(0, int(elapsed.total_seconds()))  # Ensure non-negative result
+
+def calculate_completion_status(time_spent_seconds, estimated_seconds):
+    """Calculate completion status based on time spent vs estimated time"""
+    if pd.isna(estimated_seconds) or estimated_seconds == 0:
+        return "No estimate"
+    
+    completion_ratio = time_spent_seconds / estimated_seconds
+    
+    if completion_ratio <= 1.0:
+        percentage = int(completion_ratio * 100)
+        return f"{percentage}% Complete"
+    else:
+        over_percentage = int((completion_ratio - 1.0) * 100)
+        return f"{over_percentage}% over allocation"
+
+@st.cache_data(ttl=60)
+def process_book_summary(df):
+    """Generate Book Summary Table"""
+    try:
+        grouped = df.groupby('Card name')
+
+        total_time = grouped['Time spent (s)'].sum()
+        estimated = grouped['Card estimate(s)'].max()
+        boards = grouped['Board'].first()
+
+        def get_main_user(group):
+            user_totals = group.groupby('User')['Time spent (s)'].sum()
+            return user_totals.idxmax() if not user_totals.empty else "Unknown"
+
+        main_user_series = grouped.apply(get_main_user)
+
+        completion_list = [
+            calculate_completion_status(t, 0 if pd.isna(e) else e)
+            for t, e in zip(total_time, estimated)
+        ]
+
+        df_summary = pd.DataFrame({
+            'Book Title': total_time.index,
+            'Board': boards.values,
+            'Main User': main_user_series.values,
+            'Time Spent': total_time.apply(format_seconds_to_time).values,
+            'Estimated Time': estimated.fillna(0).apply(format_seconds_to_time).values,
+            'Completion': completion_list
+        })
+
+        return df_summary.reset_index(drop=True)
+    
+    except Exception as e:
+        st.error(f"Error processing book summary: {str(e)}")
+        return pd.DataFrame()
+
+def get_most_recent_activity(df, card_name):
+    """Get the most recent list/stage worked on for a specific card"""
+    try:
+        card_data = df[df['Card name'] == card_name]
+        
+        if card_data.empty:
+            return "Unknown"
+        
+        # If Date started (f) exists, use it to find most recent
+        if 'Date started (f)' in df.columns and not card_data['Date started (f)'].isna().all():
+            # Convert dates and find the most recent entry
+            card_data_with_dates = card_data.dropna(subset=['Date started (f)'])
+            if not card_data_with_dates.empty:
+                card_data_with_dates = card_data_with_dates.copy()
+                card_data_with_dates['parsed_date'] = pd.to_datetime(card_data_with_dates['Date started (f)'], format='%m/%d/%Y', errors='coerce')
+                card_data_with_dates = card_data_with_dates.dropna(subset=['parsed_date'])
+                if not card_data_with_dates.empty:
+                    most_recent = card_data_with_dates.loc[card_data_with_dates['parsed_date'].idxmax()]
+                    return most_recent['List']
+        
+        # Fallback: return the last entry (by order in CSV)
+        return card_data.iloc[-1]['List']
+    except Exception as e:
+        return "Unknown"
+
+def create_progress_bar_html(completion_percentage):
+    """Create HTML progress bar for completion status"""
+    if completion_percentage <= 100:
+        # Normal progress (green)
+        width = min(completion_percentage, 100)
+        color = "#28a745"  # Green
+        return f"""
+        <div style="margin-bottom: 5px;">
+            <div style="background-color: #f0f0f0; border-radius: 10px; padding: 2px; width: 200px; height: 20px;">
+                <div style="background-color: {color}; width: {width}%; height: 16px; border-radius: 8px;"></div>
+            </div>
+            <div style="font-size: 12px; font-weight: bold; color: {color}; text-align: center;">
+                {completion_percentage:.1f}% complete
+            </div>
+        </div>
+        """
+    else:
+        # Over allocation (red with overflow)
+        over_percentage = completion_percentage - 100
+        return f"""
+        <div style="margin-bottom: 5px;">
+            <div style="background-color: #f0f0f0; border-radius: 10px; padding: 2px; width: 200px; height: 20px;">
+                <div style="background-color: #dc3545; width: 100%; height: 16px; border-radius: 8px;"></div>
+            </div>
+            <div style="font-size: 12px; font-weight: bold; color: #dc3545; text-align: center;">
+                {over_percentage:.1f}% over allocation
+            </div>
+        </div>
+        """
+
+def process_book_completion(df, search_filter=None):
+    """Generate Book Completion Table with visual progress"""
+    try:
+        # Apply search filter if provided
+        if search_filter:
+            # Escape special regex characters to handle punctuation properly
+            escaped_filter = re.escape(search_filter)
+            df = df[df['Card name'].str.contains(escaped_filter, case=False, na=False)]
+            
+        if df.empty:
+            return pd.DataFrame()
+        
+        # Group by book title (Card name)
+        book_groups = df.groupby('Card name')
+        
+        book_completion_data = []
+        
+        for book_title, group in book_groups:
+            # Calculate total time spent
+            total_time_spent = group['Time spent (s)'].sum()
+            
+            # Get estimated time (assuming it's the same for all rows of the same book)
+            estimated_time = 0
+            if 'Card estimate(s)' in group.columns and len(group) > 0:
+                est_val = group['Card estimate(s)'].iloc[0]
+                if not pd.isna(est_val):
+                    estimated_time = est_val
+            
+            # Get most recent activity
+            most_recent_list = get_most_recent_activity(df, book_title)
+            
+            # Calculate completion status
+            completion = calculate_completion_status(total_time_spent, estimated_time)
+            
+            # Create visual progress element
+            if estimated_time > 0:
+                completion_percentage = (total_time_spent / estimated_time) * 100
+                progress_bar_html = create_progress_bar_html(completion_percentage)
+            else:
+                progress_bar_html = '<div style="font-style: italic; color: #666;">No estimate</div>'
+            
+            visual_progress = f"""
+            <div style="padding: 10px; border: 1px solid #ddd; border-radius: 8px; margin: 2px 0; background-color: #fafafa;">
+                <div style="font-weight: bold; font-size: 14px; margin-bottom: 5px; color: #000;">{book_title}</div>
+                <div style="font-size: 12px; color: #666; margin-bottom: 8px;">Current stage: {most_recent_list}</div>
+                <div>{progress_bar_html}</div>
+            </div>
+            """
+            
+            book_completion_data.append({
+                'Book Title': book_title,
+                'Visual Progress': visual_progress,
+            })
+        
+        return pd.DataFrame(book_completion_data)
+    
+    except Exception as e:
+        st.error(f"Error processing book completion: {str(e)}")
+        return pd.DataFrame()
+
+def convert_date_format(date_str):
+    """Convert date from mm/dd/yyyy format to dd/mm/yyyy format"""
+    try:
+        if pd.isna(date_str) or date_str == 'N/A':
+            return 'N/A'
+        
+        # Parse the date string - handle both with and without time
+        if ' ' in str(date_str):
+            # Has time component
+            date_part, time_part = str(date_str).split(' ', 1)
+            date_obj = datetime.strptime(date_part, '%m/%d/%Y')
+            return f"{date_obj.strftime('%d/%m/%Y')} {time_part}"
+        else:
+            # Date only
+            date_obj = datetime.strptime(str(date_str), '%m/%d/%Y')
+            return date_obj.strftime('%d/%m/%Y')
+    except:
+        return str(date_str)  # Return original if conversion fails
+
+def process_user_task_breakdown(df):
+    """Generate User Task Breakdown Table with aggregated time"""
+    try:
+        # Check if Date started column exists in the CSV
+        has_date = 'Date started (f)' in df.columns
+        
+        if has_date:
+            # Convert date format from mm/dd/yyyy to datetime for proper sorting
+            df_copy = df.copy()
+            
+            # Try multiple date formats to handle different possible formats
+            df_copy['Date_parsed'] = pd.to_datetime(df_copy['Date started (f)'], errors='coerce')
+            
+            # If initial parsing failed, try specific formats
+            if df_copy['Date_parsed'].isna().all():
+                # Try mm/dd/yyyy format without time
+                df_copy['Date_parsed'] = pd.to_datetime(df_copy['Date started (f)'], format='%m/%d/%Y', errors='coerce')
+            
+            # Group by User, Book Title, and List to aggregate multiple sessions
+            # For each group, sum the time and take the earliest date
+            agg_funcs = {
+                'Time spent (s)': 'sum',
+                'Date_parsed': 'min',  # Get earliest date
+                'Date started (f)': 'first'  # Keep original format for fallback
+            }
+            
+            aggregated = df_copy.groupby(['User', 'Card name', 'List']).agg(agg_funcs).reset_index()
+            
+            # Convert the earliest date back to dd/mm/yyyy format for display (date only, no time)
+            def format_date_display(date_val):
+                if pd.notna(date_val):
+                    return date_val.strftime('%d/%m/%Y')
+                else:
+                    return 'N/A'
+            
+            aggregated['Date_display'] = aggregated['Date_parsed'].apply(format_date_display)
+            
+            # Rename columns for clarity
+            aggregated = aggregated[['User', 'Card name', 'List', 'Date_display', 'Time spent (s)']]
+            aggregated.columns = ['User', 'Book Title', 'List', 'Date', 'Time Spent (s)']
+            
+        else:
+            # Group by User, Book Title (Card name), and List (stage/task)
+            # Aggregate time spent for duplicate combinations
+            aggregated = df.groupby(['User', 'Card name', 'List'])['Time spent (s)'].sum().reset_index()
+            
+            # Rename columns for clarity
+            aggregated.columns = ['User', 'Book Title', 'List', 'Time Spent (s)']
+            
+            # Add empty Date column if not present
+            aggregated['Date'] = 'N/A'
+        
+        # Format time spent
+        aggregated['Time Spent'] = aggregated['Time Spent (s)'].apply(format_seconds_to_time)
+        
+        # Drop the seconds column as we now have formatted time
+        aggregated = aggregated.drop('Time Spent (s)', axis=1)
+        
+        # Reorder columns to put Date after List
+        aggregated = aggregated[['User', 'Book Title', 'List', 'Date', 'Time Spent']]
+        
+        # Sort by User → Book Title → List
+        aggregated = aggregated.sort_values(['User', 'Book Title', 'List'])
+        
+        return aggregated.reset_index(drop=True)
+    
+    except Exception as e:
+        st.error(f"Error processing user task breakdown: {str(e)}")
+        return pd.DataFrame()
+
+
+
+def main():
+    # Initialise database connection
+    engine = init_database()
+    if not engine:
+        st.error("Could not connect to database. Please check your configuration.")
+        return
+    
+    # Add custom CSS to reduce padding and margins
+    st.markdown("""
+    <style>
+    .main .block-container {
+        padding-top: 1rem;
+        padding-bottom: 1rem;
+        padding-left: 1rem;
+        padding-right: 1rem;
+    }
+    .stExpander > div:first-child {
+        padding: 0.5rem 0;
+    }
+    .element-container {
+        margin-bottom: 0.5rem;
+    }
+    div[data-testid="column"] {
+        padding: 0 0.5rem;
+    }
+    </style>
+    """, unsafe_allow_html=True)
+    
+    st.title("Book Production Time Tracking")
+    st.markdown("Track time spent on different stages of book production with detailed stage-specific analysis.")
+    
+    # Database already initialized earlier for IP authentication
+    
+    # Initialize session state for active tab
+    if 'active_tab' not in st.session_state:
+        st.session_state.active_tab = 0
+    
+    # Initialize timer session state
+    if 'timers' not in st.session_state:
+        st.session_state.timers = {}
+    if 'timer_start_times' not in st.session_state:
+        st.session_state.timer_start_times = {}
+    if 'timer_paused' not in st.session_state:
+        st.session_state.timer_paused = {}
+    if 'timer_accumulated_time' not in st.session_state:
+        st.session_state.timer_accumulated_time = {}
+    
+    # Recover any emergency saved times from previous session
+    recover_emergency_saved_times(engine)
+    
+    # Load and restore active timers from database on every page load
+    # This ensures timers are always properly restored even if session state is lost
+    active_timers = load_active_timers(engine)
+    if active_timers and 'timers_loaded' not in st.session_state:
+        st.info(f"Restored {len(active_timers)} active timer(s) from previous session.")
+        st.session_state.timers_loaded = True
+    
+    # Create tabs for different views
+    tab_names = ["Book Progress", "Add Book", "Archive", "Reporting"]
+    selected_tab = st.selectbox("Select Tab:", tab_names, index=st.session_state.active_tab, key="tab_selector")
+    
+    # Update active tab when changed - force immediate update
+    current_index = tab_names.index(selected_tab)
+    if current_index != st.session_state.active_tab:
+        st.session_state.active_tab = current_index
+        st.rerun()
+    
+    # Create individual tab sections based on selection
+    if selected_tab == "Add Book":
+        # Manual Data Entry Form
+        st.header("Manual Data Entry")
+        st.markdown("Add individual time tracking entries for detailed stage-specific analysis.")
+        
+        # Check if form should be cleared
+        clear_form = st.session_state.get('clear_form', False)
+        if clear_form:
+            # Define all form field keys that need to be cleared
+            form_keys_to_clear = [
+                "manual_card_name", "manual_board_name", "manual_tag_select", "manual_add_new_tag", "manual_new_tag",
+                # Time tracking field keys
+                "user_editorial_r&d", "time_editorial_r&d",
+                "user_editorial_writing", "time_editorial_writing", 
+                "user_1st_edit", "time_1st_edit",
+                "user_2nd_edit", "time_2nd_edit",
+                "user_design_r&d", "time_design_r&d",
+                "user_in_design", "time_in_design",
+                "user_1st_proof", "time_1st_proof",
+                "user_2nd_proof", "time_2nd_proof",
+                "user_editorial_sign_off", "time_editorial_sign_off",
+                "user_design_sign_off", "time_design_sign_off"
+            ]
+            
+            # Clear all form field keys from session state
+            for key in form_keys_to_clear:
+                if key in st.session_state:
+                    del st.session_state[key]
+            
+            # Clear the flag
+            del st.session_state['clear_form']
+        
+        # General fields
+        col1, col2 = st.columns(2)
+        with col1:
+            card_name = st.text_input("Card Name", placeholder="Enter book title", key="manual_card_name", value="" if clear_form else None)
+        with col2:
+            board_options = [
+                "Accessible Readers", 
+                "Decodable Readers", 
+                "Freedom Readers", 
+                "Graphic Readers", 
+                "Non-Fiction", 
+                "Rapid Readers (Hi-Lo)"
+            ]
+            board_name = st.selectbox("Board", options=board_options, key="manual_board_name", index=0 if clear_form else None)
+            
+        # Tag field - Multi-select
+        existing_tags = get_tags_from_database(engine)
+        
+        # Create tag input - allow selecting multiple existing or adding new
+        col1, col2 = st.columns([3, 1])
+        with col1:
+            selected_tags = st.multiselect(
+                "Tags (optional)",
+                existing_tags,
+                key="manual_tag_select",
+                placeholder="Choose an option"
+            )
+        with col2:
+            add_new_tag = st.checkbox("Add New", key="manual_add_new_tag", value=False if clear_form else None)
+        
+        # If user wants to add new tag, show text input
+        if add_new_tag:
+            new_tag = st.text_input("New Tag", placeholder="Enter new tag name", key="manual_new_tag", value="" if clear_form else None)
+            if new_tag and new_tag.strip():
+                new_tag_clean = new_tag.strip()
+                if new_tag_clean not in selected_tags:
+                    selected_tags.append(new_tag_clean)
+        
+        # Join multiple tags with commas for storage
+        final_tag = ", ".join(selected_tags) if selected_tags else None
+            
+        st.subheader("Task Assignment & Estimates")
+        st.markdown("*Assign users to stages and set time estimates. All tasks start with 0 actual time - use the Book Completion tab to track actual work time.*")
+        
+        # Define user groups for different types of work (alphabetically ordered)
+        editorial_users = ["Not set", "Bethany Latham", "Charis Mather", "Noah Leatherland", "Rebecca Phillips-Bartlett"]
+        design_users = ["Not set", "Amelia Harris", "Amy Li", "Drue Rintoul", "Jasmine Pointer", "Ker Ker Lee", "Rob Delph"]
+        
+        # Time tracking fields with specific user groups
+        time_fields = [
+            ("Editorial R&D", "Editorial R&D", editorial_users),
+            ("Editorial Writing", "Editorial Writing", editorial_users),
+            ("1st Edit", "1st Edit", editorial_users),
+            ("2nd Edit", "2nd Edit", editorial_users),
+            ("Design R&D", "Design R&D", design_users),
+            ("In Design", "In Design", design_users),
+            ("1st Proof", "1st Proof", editorial_users),
+            ("2nd Proof", "2nd Proof", editorial_users),
+            ("Editorial Sign Off", "Editorial Sign Off", editorial_users),
+            ("Design Sign Off", "Design Sign Off", design_users)
+        ]
+        
+        # Calculate and display time estimations in real-time
+        editorial_total = 0.0
+        design_total = 0.0
+        time_entries = {}
+        
+        editorial_fields = ["Editorial R&D", "Editorial Writing", "1st Edit", "2nd Edit", "1st Proof", "2nd Proof", "Editorial Sign Off"]
+        design_fields = ["Design R&D", "In Design", "Design Sign Off"]
+        
+        for field_label, list_name, user_options in time_fields:
+            st.markdown(f"**{field_label} (hours)**")
+            col1, col2 = st.columns([2, 1])
+            
+            with col1:
+                selected_user = st.selectbox(
+                    f"User for {field_label}",
+                    user_options,
+                    key=f"user_{list_name.replace(' ', '_').lower()}",
+                    label_visibility="collapsed"
+                )
+            
+            with col2:
+                time_value = st.number_input(
+                    f"Time for {field_label}",
+                    min_value=0.0,
+                    step=0.1,
+                    format="%.1f",
+                    key=f"time_{list_name.replace(' ', '_').lower()}",
+                    label_visibility="collapsed"
+                )
+            
+            # Handle user selection and calculate totals
+            # Allow time entries with or without user assignment
+            if time_value and time_value > 0:
+                final_user = selected_user if selected_user != "Not set" else None
+                
+                # Store the entry (user can be None for unassigned tasks)
+                time_entries[list_name] = {
+                    'user': final_user,
+                    'time_hours': time_value
+                }
+                
+                # Add to category totals
+                if list_name in editorial_fields:
+                    editorial_total += time_value
+                elif list_name in design_fields:
+                    design_total += time_value
+        
+        total_estimation = editorial_total + design_total
+        
+        # Display real-time calculations
+        st.markdown("---")
+        st.markdown("**Time Estimations:**")
+        st.write(f"Editorial Time Estimation: {editorial_total:.1f} hours")
+        st.write(f"Design Time Estimation: {design_total:.1f} hours")
+        st.write(f"**Total Time Estimation: {total_estimation:.1f} hours**")
+        st.markdown("---")
+        
+
+        
+        st.markdown("---")
+        
+        # Submit button outside of form
+        if st.button("Add Entry", type="primary", key="manual_submit"):
+            if not card_name:
+                st.error("Please fill in Card Name field")
+            else:
+                try:
+                    entries_added = 0
+                    current_time = datetime.now(BST)
+                    
+                    # Always create a book record first
+                    create_book_record(engine, card_name, board_name, final_tag)
+                    
+                    with engine.connect() as conn:
+                        # Add estimate entries (task assignments with 0 time spent) if any exist
+                        for list_name, entry_data in time_entries.items():
+                            # Create task entry with 0 time spent - users will use timer to track actual time
+                            # The time_hours value from the form is just for estimation display, not actual time spent
+                            
+                            # Convert hours to seconds for estimate
+                            estimate_seconds = int(entry_data['time_hours'] * 3600)
+                            
+                            # Insert into database with 0 time spent but store the estimate
+                            conn.execute(text('''
+                                INSERT INTO trello_time_tracking 
+                                (card_name, user_name, list_name, time_spent_seconds, card_estimate_seconds, board_name, created_at, session_start_time, tag)
+                                VALUES (:card_name, :user_name, :list_name, :time_spent_seconds, :card_estimate_seconds, :board_name, :created_at, :session_start_time, :tag)
+                            '''), {
+                                'card_name': card_name,
+                                'user_name': entry_data['user'],
+                                'list_name': list_name,
+                                'time_spent_seconds': 0,  # Start with 0 time spent
+                                'card_estimate_seconds': estimate_seconds,  # Store the estimate
+                                'board_name': board_name if board_name else None,
+                                'created_at': current_time,
+                                'session_start_time': None,  # No active session for manual entries
+                                'tag': final_tag
+                            })
+                            entries_added += 1
+                        
+                        conn.commit()
+                    
+                    # Keep user on the Add Book tab
+                    st.session_state.active_tab = 1  # Add Book tab
+                    
+                    if entries_added > 0:
+                        # Store success message in session state for permanent display
+                        st.session_state.book_created_message = f"Book '{card_name}' created successfully with {entries_added} time estimates!"
+                    else:
+                        # Book created without tasks
+                        st.session_state.book_created_message = f"Book '{card_name}' created successfully! You can add tasks later from the Book Progress tab."
+                    
+                    # Set flag to clear form on next render instead of modifying session state directly
+                    st.session_state.clear_form = True
+                        
+                except Exception as e:
+                    st.error(f"Error adding manual entry: {str(e)}")
+        
+        # Show permanent success message if book was created (below the button)
+        if 'book_created_message' in st.session_state:
+            st.success(st.session_state.book_created_message)
+    
+    elif selected_tab == "Book Progress":
+        # Header with hover clipboard functionality
+        st.markdown("""
+        <div style="position: relative; display: inline-block;">
+            <h1 style="display: inline-block; margin: 0;" id="book-completion-progress">Book Completion Progress</h1>
+            <span class="header-copy-icon" style="
+                opacity: 0;
+                transition: opacity 0.2s;
+                margin-left: 10px;
+                cursor: pointer;
+                color: #666;
+                font-size: 20px;
+                vertical-align: middle;
+            " onclick="copyHeaderLink()">🔗</span>
+        </div>
+        <style>
+        #book-completion-progress:hover + .header-copy-icon,
+        .header-copy-icon:hover {
+            opacity: 1;
+        }
+        </style>
+        <script>
+        function copyHeaderLink() {
+            const url = window.location.origin + window.location.pathname + '#book-completion-progress';
+            navigator.clipboard.writeText(url).then(function() {
+                console.log('Copied header link to clipboard');
+            });
+        }
+        </script>
+        """, unsafe_allow_html=True)
+        st.markdown("Visual progress tracking for all books with individual task timers.")
+        
+        # Display active timers - simplified (card names only)
+        active_timer_count = sum(1 for running in st.session_state.timers.values() if running)
+        if active_timer_count > 0:
+            st.info(f"{active_timer_count} timer(s) currently running")
+            
+            # Show active timer card names only
+            st.markdown("### Active Timers")
+            active_books = set()
+            for task_key, is_running in st.session_state.timers.items():
+                if is_running and task_key in st.session_state.timer_start_times:
+                    # Extract book name from task_key
+                    parts = task_key.split('_')
+                    if len(parts) >= 3:
+                        book_title = '_'.join(parts[:-2])
+                        active_books.add(book_title)
+            
+            # Display unique book names with timers
+            for book_title in sorted(active_books):
+                st.write(f"**{book_title}**")
+            
+            st.markdown("---")
+        
+        # Initialize session state for timers
+        if 'timers' not in st.session_state:
+            st.session_state.timers = {}
+        if 'timer_start_times' not in st.session_state:
+            st.session_state.timer_start_times = {}
+        
+        # Check if we have data from database with SSL connection retry
+        total_records = 0
+        max_retries = 3
+        for attempt in range(max_retries):
+            try:
+                with engine.connect() as conn:
+                    result = conn.execute(text("SELECT COUNT(*) FROM trello_time_tracking"))
+                    total_records = result.scalar()
+                    break  # Success, exit retry loop
+            except Exception as e:
+                if attempt < max_retries - 1:
+                    # Try to recreate engine connection
+                    time.sleep(0.5)  # Brief pause before retry
+                    continue
+                else:
+                    # Final attempt failed, show error but continue
+                    st.error(f"Database connection issue (attempt {attempt + 1}): {str(e)[:100]}...")
+                    total_records = 0
+                    break
+        
+        try:
+            # Clear pending refresh state at start of render
+            if 'pending_refresh' in st.session_state:
+                del st.session_state.pending_refresh
+            
+            # Initialize variables to avoid UnboundLocalError
+            df_from_db = None
+            all_books = []
+            
+            if total_records and total_records > 0:
+                
+                # Get all books including those without tasks
+                all_books = get_all_books(engine)
+                
+                # Get task data from database for book completion (exclude archived)
+                df_from_db = pd.read_sql(
+                    '''SELECT card_name as "Card name", 
+                       COALESCE(user_name, 'Not set') as "User", 
+                       list_name as "List", 
+                       time_spent_seconds as "Time spent (s)", 
+                       date_started as "Date started (f)", 
+                       card_estimate_seconds as "Card estimate(s)", 
+                       board_name as "Board", created_at, tag as "Tag"
+                       FROM trello_time_tracking WHERE archived = FALSE ORDER BY created_at DESC''', 
+                    engine
+                )
+                
+                if not df_from_db.empty:
+                    # Calculate total books for search title
+                    books_with_tasks = set(df_from_db['Card name'].unique()) if not df_from_db.empty else set()
+                    books_without_tasks = set(book[0] for book in all_books if book[0] not in books_with_tasks)
+                    total_books = len(books_with_tasks | books_without_tasks)
+                    
+                    # Add search bar only
+                    search_query = st.text_input(
+                        f"Search books by title ({total_books}):",
+                        placeholder="Enter book title to search...",
+                        key="completion_search"
+                    )
+                    
+                    # Initialize filtered_df
+                    filtered_df = df_from_db.copy()
+                    
+                    # Determine what to display based on search only
+                    if search_query:
+                        # Filter books based on search and limit to 10 results
+                        # Escape special regex characters in search query
+                        import re
+                        escaped_query = re.escape(search_query)
+                        mask = filtered_df['Card name'].str.contains(escaped_query, case=False, na=False)
+                        filtered_df = filtered_df[mask]
+                        
+                        # Get unique books from both sources
+                        books_with_tasks = set(filtered_df['Card name'].unique()) if not filtered_df.empty else set()
+                        books_without_tasks = set(book[0] for book in all_books if book[0] not in books_with_tasks)
+                        
+                        # Filter books without tasks based on search query
+                        books_without_tasks = {book for book in books_without_tasks if search_query.lower() in book.lower()}
+                        
+                        # Combine and sort, then limit to 10
+                        all_matching_books = sorted(books_with_tasks | books_without_tasks)
+                        books_to_display = all_matching_books[:10]  # Limit to 10 results
+                        
+                        if len(books_to_display) == 0:
+                            books_to_display = []
+                    else:
+                        books_to_display = []
+                    
+                    # Only display books if we have search results
+                    if books_to_display:
+                            # Display each book with enhanced visualization
+                            for book_title in books_to_display:
+                                # Check if book has tasks
+                                if not filtered_df.empty:
+                                    book_mask = filtered_df['Card name'] == book_title
+                                    book_data = filtered_df[book_mask].copy()
+                                else:
+                                    book_data = pd.DataFrame()
+                                
+                                # Debug: Let's see what we have
+                                # st.write(f"DEBUG: Book '{book_title}' - book_data shape: {book_data.shape}")
+                                # if not book_data.empty:
+                                #     st.write(f"DEBUG: Book tasks found: {book_data['List'].unique()}")
+                                # else:
+                                #     st.write(f"DEBUG: Book data is empty for '{book_title}'")
+                                
+                                # If book has no tasks, create empty data structure
+                                if book_data.empty:
+                                    # Get book info from all_books
+                                    book_info = next((book for book in all_books if book[0] == book_title), None)
+                                    if book_info:
+                                        # Create minimal book data structure
+                                        book_data = pd.DataFrame({
+                                            'Card name': [book_title],
+                                            'User': ['Not set'],
+                                            'List': ['No tasks assigned'],
+                                            'Time spent (s)': [0],
+                                            'Date started (f)': [None],
+                                            'Card estimate(s)': [0],
+                                            'Board': [book_info[1] if book_info[1] else 'Not set'],
+                                            'Tag': [book_info[2] if book_info[2] else None]
+                                        })
+                                
+                                # Calculate overall progress using stage-based estimates
+                                total_time_spent = book_data['Time spent (s)'].sum()
+                                
+                                # Calculate total estimated time from the database entries
+                                # Sum up all estimates stored in the database for this book
+                                estimated_time = 0
+                                if 'Card estimate(s)' in book_data.columns:
+                                    book_estimates = book_data['Card estimate(s)'].fillna(0).sum()
+                                    if book_estimates > 0:
+                                        estimated_time = book_estimates
+                                
+                                # If no estimates in database, use reasonable defaults per stage
+                                if estimated_time == 0:
+                                    default_stage_estimates = {
+                                        'Editorial R&D': 2 * 3600,        # 2 hours default
+                                        'Editorial Writing': 8 * 3600,    # 8 hours default 
+                                        '1st Edit': 4 * 3600,             # 4 hours default
+                                        '2nd Edit': 2 * 3600,             # 2 hours default
+                                        'Design R&D': 3 * 3600,           # 3 hours default
+                                        'In Design': 6 * 3600,            # 6 hours default
+                                        '1st Proof': 2 * 3600,            # 2 hours default
+                                        '2nd Proof': 1.5 * 3600,          # 1.5 hours default
+                                        'Editorial Sign Off': 0.5 * 3600, # 30 minutes default
+                                        'Design Sign Off': 0.5 * 3600     # 30 minutes default
+                                    }
+                                    unique_stages = book_data['List'].unique()
+                                    estimated_time = sum(default_stage_estimates.get(stage, 3600) for stage in unique_stages)
+                                
+                                # Calculate completion percentage for display
+                                if estimated_time > 0:
+                                    completion_percentage = (total_time_spent / estimated_time) * 100
+                                    progress_text = f"{format_seconds_to_time(total_time_spent)}/{format_seconds_to_time(estimated_time)} ({completion_percentage:.1f}%)"
+                                else:
+                                    completion_percentage = 0
+                                    progress_text = f"Total: {format_seconds_to_time(total_time_spent)} (No estimate)"
+                                
+                                # Check for active timers more efficiently
+                                has_active_timer = any(
+                                    timer_key.startswith(f"{book_title}_") and active 
+                                    for timer_key, active in st.session_state.timers.items()
+                                )
+                                
+                                # Check if all tasks are completed (only if book has tasks)
+                                all_tasks_completed = False
+                                completion_emoji = ""
+                                if not book_data.empty and book_data['List'].iloc[0] != 'No tasks assigned':
+                                    # Check completion status from database
+                                    all_tasks_completed = check_all_tasks_completed(engine, book_title)
+                                    completion_emoji = "✅ " if all_tasks_completed else ""
+                                
+                                # Create book title with progress percentage
+                                if estimated_time > 0:
+                                    if completion_percentage > 100:
+                                        over_percentage = completion_percentage - 100
+                                        book_title_with_progress = f"{completion_emoji}**{book_title}** ({over_percentage:.1f}% over estimate)"
+                                    else:
+                                        book_title_with_progress = f"{completion_emoji}**{book_title}** ({completion_percentage:.1f}%)"
+                                else:
+                                    book_title_with_progress = f"{completion_emoji}**{book_title}** (No estimate)"
+                                
+                                # Check if book should be expanded (either has active timer or was manually expanded)
+                                expanded_key = f"expanded_{book_title}"
+                                if expanded_key not in st.session_state:
+                                    st.session_state[expanded_key] = has_active_timer
+                                
+                                with st.expander(book_title_with_progress, expanded=st.session_state[expanded_key]):
+                                    # Show progress bar and completion info at the top
+                                    progress_bar_html = f"""
+                                    <div style="width: 50%; background-color: #f0f0f0; border-radius: 5px; height: 10px; margin: 8px 0;">
+                                        <div style="width: {min(completion_percentage, 100):.1f}%; background-color: #007bff; height: 100%; border-radius: 5px;"></div>
+                                    </div>
+                                    """
+                                    st.markdown(progress_bar_html, unsafe_allow_html=True)
+                                    st.markdown(f'<div style="font-size: 14px; color: #666; margin-bottom: 10px;">{progress_text}</div>', unsafe_allow_html=True)
+                                    
+                                    # Display tag if available
+                                    book_tags = book_data['Tag'].dropna().unique()
+                                    if len(book_tags) > 0 and book_tags[0]:
+                                        # Handle multiple tags (comma-separated)
+                                        tag_display = book_tags[0]
+                                        # If there are commas, it means multiple tags
+                                        if ',' in tag_display:
+                                            tag_display = tag_display.replace(',', ', ')  # Ensure proper spacing
+                                        st.markdown(f'<div style="font-size: 14px; color: #888; margin-bottom: 10px;"><strong>Tags:</strong> {tag_display}</div>', unsafe_allow_html=True)
+                                    
+                                    st.markdown("---")
+                                    
+                                    # Define the order of stages to match the actual data entry form
+                                    stage_order = [
+                                        'Editorial R&D', 'Editorial Writing', '1st Edit', '2nd Edit',
+                                        'Design R&D', 'In Design', '1st Proof', '2nd Proof', 
+                                        'Editorial Sign Off', 'Design Sign Off'
+                                    ]
+                                    
+                                    # Group by stage/list and aggregate by user
+                                    stages_grouped = book_data.groupby('List')
+                                    
+                                    # Display stages in accordion style (each stage as its own expander)
+                                    stage_counter = 0
+                                    for stage_name in stage_order:
+                                        if stage_name in stages_grouped.groups:
+                                            stage_data = stages_grouped.get_group(stage_name)
+                                            
+                                            # Check if this stage has any active timers (efficient lookup)
+                                            stage_has_active_timer = any(
+                                                timer_key.startswith(f"{book_title}_{stage_name}_") and active 
+                                                for timer_key, active in st.session_state.timers.items()
+                                            )
+                                            
+                                            # Aggregate time by user for this stage
+                                            user_aggregated = stage_data.groupby('User')['Time spent (s)'].sum().reset_index()
+                                            
+                                            # Create a summary for the expander title showing all users and their progress
+                                            stage_summary_parts = []
+                                            for idx, user_task in user_aggregated.iterrows():
+                                                user_name = user_task['User']
+                                                actual_time = user_task['Time spent (s)']
+                                                
+                                                # Get estimated time from the database for this specific user/stage combination
+                                                user_stage_data = stage_data[stage_data['User'] == user_name]
+                                                estimated_time_for_user = 3600  # Default 1 hour
+                                                
+                                                if not user_stage_data.empty and 'Card estimate(s)' in user_stage_data.columns:
+                                                    # Find the first record that has a non-null, non-zero estimate
+                                                    estimates = user_stage_data['Card estimate(s)'].dropna()
+                                                    non_zero_estimates = estimates[estimates > 0]
+                                                    if not non_zero_estimates.empty:
+                                                        estimated_time_for_user = non_zero_estimates.iloc[0]
+                                                
+                                                # Check if task is completed and add tick emoji
+                                                task_completed = get_task_completion(engine, book_title, user_name, stage_name)
+                                                completion_emoji = "✅ " if task_completed else ""
+                                                
+                                                # Format times for display
+                                                actual_time_str = format_seconds_to_time(actual_time)
+                                                estimated_time_str = format_seconds_to_time(estimated_time_for_user)
+                                                user_display = user_name if user_name and user_name != "Not set" else "Unassigned"
+                                                
+                                                stage_summary_parts.append(f"{user_display} | {actual_time_str}/{estimated_time_str} {completion_emoji}".rstrip())
+                                            
+                                            # Create expander title with stage name and user summaries
+                                            if stage_summary_parts:
+                                                expander_title = f"**{stage_name}** | " + " | ".join(stage_summary_parts)
+                                            else:
+                                                expander_title = stage_name
+                                            
+                                            # Check if stage should be expanded (either has active timer or was manually expanded)
+                                            stage_expanded_key = f"stage_expanded_{book_title}_{stage_name}"
+                                            if stage_expanded_key not in st.session_state:
+                                                st.session_state[stage_expanded_key] = stage_has_active_timer
+                                            
+                                            with st.expander(expander_title, expanded=st.session_state[stage_expanded_key]):
+                                                # Show one task per user for this stage
+                                                for idx, user_task in user_aggregated.iterrows():
+                                                    user_name = user_task['User']
+                                                    actual_time = user_task['Time spent (s)']
+                                                    task_key = f"{book_title}_{stage_name}_{user_name}"
+                                                    
+                                                    # Get estimated time from the database for this specific user/stage combination
+                                                    user_stage_data = stage_data[stage_data['User'] == user_name]
+                                                    estimated_time_for_user = 3600  # Default 1 hour
+                                                    
+                                                    if not user_stage_data.empty and 'Card estimate(s)' in user_stage_data.columns:
+                                                        # Find the first record that has a non-null, non-zero estimate
+                                                        estimates = user_stage_data['Card estimate(s)'].dropna()
+                                                        non_zero_estimates = estimates[estimates > 0]
+                                                        if not non_zero_estimates.empty:
+                                                            estimated_time_for_user = non_zero_estimates.iloc[0]
+                                                    
+                                                    # Create columns for task info and timer
+                                                    col1, col2, col3 = st.columns([4, 1, 3])
+                                                    
+                                                    with col1:
+                                                        # User assignment dropdown
+                                                        current_user = user_name if user_name else "Not set"
+                                                        
+                                                        # Determine user options based on stage type
+                                                        if stage_name in ["Editorial R&D", "Editorial Writing", "1st Edit", "2nd Edit", "1st Proof", "2nd Proof", "Editorial Sign Off"]:
+                                                            user_options = ["Not set", "Bethany Latham", "Charis Mather", "Noah Leatherland", "Rebecca Phillips-Bartlett"]
+                                                        else:  # Design stages
+                                                            user_options = ["Not set", "Amelia Harris", "Amy Li", "Drue Rintoul", "Jasmine Pointer", "Ker Ker Lee", "Rob Delph"]
+                                                        
+                                                        # Find current user index
+                                                        try:
+                                                            current_index = user_options.index(current_user)
+                                                        except ValueError:
+                                                            current_index = 0  # Default to "Not set"
+                                                        
+                                                        # Use a stable key that doesn't depend on user_name to avoid state conflicts
+                                                        selectbox_key = f"reassign_{book_title}_{stage_name}"
+                                                        
+                                                        new_user = st.selectbox(
+                                                            f"User for {stage_name}:",
+                                                            user_options,
+                                                            index=current_index,
+                                                            key=selectbox_key
+                                                        )
+                                                        
+                                                        # Display progress information directly under user dropdown
+                                                        if user_name and user_name != "Not set":
+                                                            # Use the actual_time variable that's already calculated for this user/stage
+                                                            if estimated_time_for_user and estimated_time_for_user > 0:
+                                                                progress_percentage = actual_time / estimated_time_for_user
+                                                                time_spent_formatted = format_seconds_to_time(actual_time)
+                                                                estimated_formatted = format_seconds_to_time(estimated_time_for_user)
+                                                                
+                                                                # Progress bar
+                                                                progress_value = max(0.0, min(progress_percentage, 1.0))
+                                                                st.progress(progress_value)
+                                                                
+                                                                # Progress text
+                                                                if progress_percentage > 1.0:
+                                                                    st.write(f"{(progress_percentage - 1) * 100:.1f}% over estimate")
+                                                                elif progress_percentage == 1.0:
+                                                                    st.write("COMPLETE: 100%")
+                                                                else:
+                                                                    st.write(f"{progress_percentage * 100:.1f}% complete")
+                                                                
+                                                                # Time information
+                                                                st.write(f"Time: {time_spent_formatted} / {estimated_formatted}")
+                                                                
+                                                                # Completion checkbox - always get fresh status from database
+                                                                completion_key = f"complete_{book_title}_{stage_name}_{user_name}"
+                                                                current_completion_status = get_task_completion(engine, book_title, user_name, stage_name)
+                                                                
+                                                                # Update session state with database value
+                                                                st.session_state[completion_key] = current_completion_status
+                                                                
+                                                                new_completion_status = st.checkbox(
+                                                                    "Completed",
+                                                                    value=current_completion_status,
+                                                                    key=f"checkbox_{completion_key}"
+                                                                )
+                                                                
+                                                                # Update completion status if changed
+                                                                if new_completion_status != current_completion_status:
+                                                                    update_task_completion(engine, book_title, user_name, stage_name, new_completion_status)
+                                                                    # Update session state immediately
+                                                                    st.session_state[completion_key] = new_completion_status
+                                                                    
+                                                                    # Clear any cached completion status to force refresh
+                                                                    completion_cache_key = f"book_completion_{book_title}"
+                                                                    if completion_cache_key in st.session_state:
+                                                                        del st.session_state[completion_cache_key]
+                                                                    
+                                                                    # Store success message for display without immediate refresh
+                                                                    success_msg_key = f"completion_success_{task_key}"
+                                                                    status_text = "✅ Marked as completed" if new_completion_status else "❌ Marked as incomplete" 
+                                                                    st.session_state[success_msg_key] = status_text
+                                                                    
+                                                                    # Set flag for book-level completion update
+                                                                    st.session_state['completion_changed'] = True
+                                                            else:
+                                                                st.write("No time estimate set")
+                                                        
+                                                        # Handle user reassignment with improved state management
+                                                        if new_user != current_user:
+                                                            try:
+                                                                with engine.connect() as conn:
+                                                                    # Update user assignment in database
+                                                                    new_user_value = new_user if new_user != "Not set" else None
+                                                                    old_user_value = user_name if user_name != "Not set" else None
+                                                                    
+                                                                    conn.execute(text('''
+                                                                        UPDATE trello_time_tracking 
+                                                                        SET user_name = :new_user
+                                                                        WHERE card_name = :card_name 
+                                                                        AND list_name = :list_name 
+                                                                        AND COALESCE(user_name, '') = COALESCE(:old_user, '')
+                                                                    '''), {
+                                                                        'new_user': new_user_value,
+                                                                        'card_name': book_title,
+                                                                        'list_name': stage_name,
+                                                                        'old_user': old_user_value
+                                                                    })
+                                                                    conn.commit()
+                                                                    
+                                                                    # Clear relevant session state to force refresh
+                                                                    keys_to_clear = [k for k in st.session_state.keys() 
+                                                                                    if book_title in k and stage_name in k]
+                                                                    for key in keys_to_clear:
+                                                                        if key.startswith(('complete_', 'timer_')):
+                                                                            del st.session_state[key]
+                                                                    
+                                                                    # Store success message instead of immediate refresh
+                                                                    success_key = f"reassign_success_{book_title}_{stage_name}"
+                                                                    st.session_state[success_key] = f"User reassigned from {current_user} to {new_user}"
+                                                                    
+                                                                    # User reassignment completed
+                                                            except Exception as e:
+                                                                st.error(f"Error reassigning user: {str(e)}")
+                                                    
+
+                                            
+                                            with col2:
+                                                # Empty space - timer moved to button column
+                                                st.write("")
+                                            
+                                            with col3:
+                                                # Start/Stop timer button with timer display
+                                                if task_key not in st.session_state.timers:
+                                                    st.session_state.timers[task_key] = False
+                                                
+                                                # Timer controls and display
+                                                if st.session_state.timers[task_key]:
+                                                    # Timer is active - show simple stop control
+                                                    if task_key in st.session_state.timer_start_times:
+                                                        
+                                                        # Simple timer calculation
+                                                        start_time = st.session_state.timer_start_times[task_key]
+                                                        elapsed_seconds = calculate_timer_elapsed_time(start_time)
+                                                        elapsed_str = format_seconds_to_time(elapsed_seconds)
+                                                        
+                                                        # Display recording status with layout: Recording (hh:mm:ss) -> (Stop Button)
+                                                        timer_row1_col1, timer_row1_col2 = st.columns([2, 1])
+                                                        with timer_row1_col1:
+                                                            st.write(f"**Recording** ({elapsed_str})")
+                                                        
+                                                        with timer_row1_col2:
+                                                            if st.button("Stop", key=f"stop_{task_key}"):
+                                                                # Calculate final total time
+                                                                final_time = elapsed_seconds
+                                                                
+                                                                # Keep expanded states
+                                                                expanded_key = f"expanded_{book_title}"
+                                                                st.session_state[expanded_key] = True
+                                                                stage_expanded_key = f"stage_expanded_{book_title}_{stage_name}"
+                                                                st.session_state[stage_expanded_key] = True
+                                                                
+                                                                # Always clear timer states first to prevent double-processing
+                                                                st.session_state.timers[task_key] = False
+                                                                timer_start_time = st.session_state.timer_start_times.get(task_key)
+                                                                
+                                                                # Save to database only if time > 0
+                                                                if final_time > 0 and timer_start_time:
+                                                                    try:
+                                                                        user_original_data = stage_data[stage_data['User'] == user_name].iloc[0]
+                                                                        board_name = user_original_data['Board']
+                                                                        existing_tag = user_original_data.get('Tag', None) if 'Tag' in user_original_data else None
+                                                                        
+                                                                        with engine.connect() as conn:
+                                                                            # Use ON CONFLICT to handle duplicate entries by updating existing records
+                                                                            conn.execute(text('''
+                                                                                INSERT INTO trello_time_tracking 
+                                                                                (card_name, user_name, list_name, time_spent_seconds, 
+                                                                                 date_started, session_start_time, board_name, tag)
+                                                                                VALUES (:card_name, :user_name, :list_name, :time_spent_seconds, 
+                                                                                       :date_started, :session_start_time, :board_name, :tag)
+                                                                                ON CONFLICT (card_name, user_name, list_name, date_started, time_spent_seconds) 
+                                                                                DO UPDATE SET 
+                                                                                    session_start_time = EXCLUDED.session_start_time,
+                                                                                    board_name = EXCLUDED.board_name,
+                                                                                    tag = EXCLUDED.tag,
+                                                                                    created_at = CURRENT_TIMESTAMP
+                                                                            '''), {
+                                                                                'card_name': book_title,
+                                                                                'user_name': user_name,
+                                                                                'list_name': stage_name,
+                                                                                'time_spent_seconds': final_time,
+                                                                                'date_started': timer_start_time.date(),
+                                                                                'session_start_time': timer_start_time,
+                                                                                'board_name': board_name,
+                                                                                'tag': existing_tag
+                                                                            })
+                                                                            
+                                                                            # Remove from active timers
+                                                                            conn.execute(text('DELETE FROM active_timers WHERE timer_key = :timer_key'), 
+                                                                                       {'timer_key': task_key})
+                                                                            conn.commit()
+                                                                            
+                                                                        # Store success message for display at bottom
+                                                                        success_msg_key = f"timer_success_{task_key}"
+                                                                        st.session_state[success_msg_key] = f"Added {elapsed_str} to {book_title} - {stage_name}"
+                                                                        
+                                                                        # Timer stopped successfully
+                                                                    except Exception as e:
+                                                                        st.error(f"Error saving timer data: {str(e)}")
+                                                                        # Still try to clean up active timer from database on error
+                                                                        try:
+                                                                            with engine.connect() as conn:
+                                                                                conn.execute(text('DELETE FROM active_timers WHERE timer_key = :timer_key'), 
+                                                                                           {'timer_key': task_key})
+                                                                                conn.commit()
+                                                                        except:
+                                                                            pass  # Ignore cleanup errors
+                                                                else:
+                                                                    # Even if no time to save, clean up active timer
+                                                                    try:
+                                                                        with engine.connect() as conn:
+                                                                            conn.execute(text('DELETE FROM active_timers WHERE timer_key = :timer_key'), 
+                                                                                       {'timer_key': task_key})
+                                                                            conn.commit()
+                                                                    except:
+                                                                        pass  # Ignore cleanup errors
+                                                                
+                                                                # Clear timer states
+                                                                if task_key in st.session_state.timer_start_times:
+                                                                    del st.session_state.timer_start_times[task_key]
+                                                        
+
+                                                    else:
+                                                        st.write("")
+                                                else:
+                                                    # Timer is not active - show Start button
+                                                    if st.button("Start", key=f"start_{task_key}"):
+                                                        # Preserve expanded state before rerun
+                                                        expanded_key = f"expanded_{book_title}"
+                                                        st.session_state[expanded_key] = True
+                                                        
+                                                        # Also preserve stage expanded state
+                                                        stage_expanded_key = f"stage_expanded_{book_title}_{stage_name}"
+                                                        st.session_state[stage_expanded_key] = True
+                                                        
+                                                        # Start timer - use UTC for consistency
+                                                        start_time_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
+                                                        # Convert to BST for display/storage but keep UTC calculation base
+                                                        start_time_bst = start_time_utc.astimezone(BST)
+                                                        st.session_state.timers[task_key] = True
+                                                        st.session_state.timer_start_times[task_key] = start_time_bst
+                                                        
+                                                        # Save to database for persistence
+                                                        user_original_data = stage_data[stage_data['User'] == user_name].iloc[0]
+                                                        board_name = user_original_data['Board']
+                                                        
+                                                        save_active_timer(
+                                                            engine, task_key, book_title, 
+                                                            user_name if user_name != "Not set" else None,
+                                                            stage_name, board_name, start_time_bst
+                                                        )
+                                                        
+                                                        st.rerun()
+                                                
+                                                # Manual time entry section
+                                                st.write("**Manual Entry:**")
+                                                
+                                                # Create a form to handle Enter key properly
+                                                with st.form(key=f"time_form_{task_key}"):
+                                                    manual_time = st.text_input(
+                                                        "Add time (hh:mm:ss):", 
+                                                        placeholder="01:30:00"
+                                                    )
+                                                    
+                                                    # Hide the submit button and form styling with CSS
+                                                    st.markdown("""
+                                                    <style>
+                                                    div[data-testid="stForm"] button {
+                                                        display: none;
+                                                    }
+                                                    div[data-testid="stForm"] {
+                                                        border: none !important;
+                                                        background: none !important;
+                                                        padding: 0 !important;
+                                                    }
+                                                    </style>
+                                                    """, unsafe_allow_html=True)
+                                                    
+                                                    submitted = st.form_submit_button("Add Time")
+                                                    
+                                                    if submitted and manual_time:
+                                                        try:
+                                                            # Parse the time format hh:mm:ss
+                                                            time_parts = manual_time.split(':')
+                                                            if len(time_parts) == 3:
+                                                                hours = int(time_parts[0])
+                                                                minutes = int(time_parts[1])
+                                                                seconds = int(time_parts[2])
+                                                                
+                                                                # Validate individual components
+                                                                if hours > 100:
+                                                                    st.error(f"Maximum hours allowed is 100. You entered {hours} hours.")
+                                                                elif minutes >= 60:
+                                                                    st.error(f"Minutes must be less than 60. You entered {minutes} minutes.")
+                                                                elif seconds >= 60:
+                                                                    st.error(f"Seconds must be less than 60. You entered {seconds} seconds.")
+                                                                else:
+                                                                    total_seconds = hours * 3600 + minutes * 60 + seconds
+                                                                    
+                                                                    # Validate maximum time (100 hours = 360,000 seconds)
+                                                                    max_seconds = 100 * 3600  # 360,000 seconds
+                                                                    if total_seconds > max_seconds:
+                                                                        st.error(f"Maximum time allowed is 100:00:00. You entered {manual_time}")
+                                                                    elif total_seconds > 0:
+                                                                        # Add manual time to database
+                                                                        try:
+                                                                            # Get board name from original data
+                                                                            user_original_data = stage_data[stage_data['User'] == user_name].iloc[0]
+                                                                            board_name = user_original_data['Board']
+                                                                            # Get existing tag from original data
+                                                                            existing_tag = user_original_data.get('Tag', None) if 'Tag' in user_original_data else None
+                                                                            
+                                                                            # Get current completion status to preserve it
+                                                                            completion_key = f"complete_{book_title}_{stage_name}_{user_name}"
+                                                                            current_completion = get_task_completion(engine, book_title, user_name, stage_name)
+                                                                            # Also check session state in case it was just changed
+                                                                            if completion_key in st.session_state:
+                                                                                current_completion = st.session_state[completion_key]
+                                                                            
+                                                                            # Preserve expanded state before rerun
+                                                                            expanded_key = f"expanded_{book_title}"
+                                                                            st.session_state[expanded_key] = True
+                                                                            
+                                                                            # Preserve stage expanded state
+                                                                            stage_expanded_key = f"stage_expanded_{book_title}_{stage_name}"
+                                                                            st.session_state[stage_expanded_key] = True
+                                                                            
+                                                                            with engine.connect() as conn:
+                                                                                conn.execute(text('''
+                                                                                    INSERT INTO trello_time_tracking 
+                                                                                    (card_name, user_name, list_name, time_spent_seconds, board_name, created_at, tag, completed)
+                                                                                    VALUES (:card_name, :user_name, :list_name, :time_spent_seconds, :board_name, :created_at, :tag, :completed)
+                                                                                '''), {
+                                                                                    'card_name': book_title,
+                                                                                    'user_name': user_name,
+                                                                                    'list_name': stage_name,
+                                                                                    'time_spent_seconds': total_seconds,
+                                                                                    'board_name': board_name,
+                                                                                    'created_at': datetime.now(BST),
+                                                                                    'tag': existing_tag,
+                                                                                    'completed': current_completion
+                                                                                })
+                                                                                conn.commit()
+                                                                            
+                                                                            # Store success message in session state for display
+                                                                            success_msg_key = f"manual_time_success_{task_key}"
+                                                                            st.session_state[success_msg_key] = f"Added {manual_time} to progress"
+                                                                            
+                                                                        except Exception as e:
+                                                                            st.error(f"Error saving time: {str(e)}")
+                                                                    else:
+                                                                        st.error("Time must be greater than 00:00:00")
+                                                            else:
+                                                                st.error("Please use format hh:mm:ss (e.g., 01:30:00)")
+                                                        except ValueError:
+                                                            st.error("Please enter valid numbers in hh:mm:ss format")
+                                                
+
+                                                
+                                                # Display various success messages
+                                                # Timer success message
+                                                timer_success_key = f"timer_success_{task_key}"
+                                                if timer_success_key in st.session_state:
+                                                    st.success(st.session_state[timer_success_key])
+                                                    del st.session_state[timer_success_key]
+                                                
+                                                # Manual time success message
+                                                manual_success_key = f"manual_time_success_{task_key}"
+                                                if manual_success_key in st.session_state:
+                                                    st.success(st.session_state[manual_success_key])
+                                                    del st.session_state[manual_success_key]
+                                                
+                                                # Completion status success message
+                                                completion_success_key = f"completion_success_{task_key}"
+                                                if completion_success_key in st.session_state:
+                                                    st.success(st.session_state[completion_success_key])
+                                                    del st.session_state[completion_success_key]
+                                                
+                                                # User reassignment success message
+                                                reassign_success_key = f"reassign_success_{book_title}_{stage_name}"
+                                                if reassign_success_key in st.session_state:
+                                                    st.success(st.session_state[reassign_success_key])
+                                                    del st.session_state[reassign_success_key]
+
+                                    
+                                    # Show count of running timers (refresh buttons now appear under individual timers)
+                                    running_timers = [k for k, v in st.session_state.timers.items() if v and book_title in k]
+                                    if running_timers:
+                                        st.write(f"{len(running_timers)} timer(s) running")
+                                    
+                                    # Add stage dropdown
+                                    available_stages = get_available_stages_for_book(engine, book_title)
+                                    if available_stages:
+                                        st.markdown("---")
+                                        col1, col2 = st.columns([3, 1])
+                                        
+                                        with col1:
+                                            selected_stage = st.selectbox(
+                                                "Add stage:",
+                                                options=["Select a stage to add..."] + available_stages,
+                                                key=f"add_stage_{book_title}"
+                                            )
+                                        
+                                        with col2:
+                                            time_estimate = st.number_input(
+                                                "Hours:",
+                                                min_value=0.0,
+                                                step=0.1,
+                                                format="%.1f",
+                                                value=1.0,
+                                                key=f"add_stage_time_{book_title}",
+                                                on_change=None  # Prevent automatic refresh
+                                            )
+                                        
+                                        if selected_stage != "Select a stage to add...":
+                                            # Get the current time estimate from session state
+                                            time_estimate_key = f"add_stage_time_{book_title}"
+                                            current_time_estimate = st.session_state.get(time_estimate_key, 1.0)
+                                            
+                                            # Get book info for board name and tag
+                                            book_info = next((book for book in all_books if book[0] == book_title), None)
+                                            board_name = book_info[1] if book_info else None
+                                            tag = book_info[2] if book_info else None
+                                            
+                                            # Convert hours to seconds for estimate
+                                            estimate_seconds = int(current_time_estimate * 3600)
+                                            
+                                            if add_stage_to_book(engine, book_title, selected_stage, board_name, tag, estimate_seconds):
+                                                st.success(f"Added {selected_stage} to {book_title} with {current_time_estimate} hour estimate")
+                                                # Stage added successfully
+                                            else:
+                                                st.error("Failed to add stage")
+                                    
+                                    # Remove stage section at the bottom left of each book
+                                    if stages_grouped.groups:  # Only show if book has stages
+                                        st.markdown("---")
+                                        remove_col1, remove_col2, remove_col3 = st.columns([2, 1, 1])
+                                        
+                                        with remove_col1:
+                                            # Get all current stages for this book
+                                            current_stages_with_users = []
+                                            for stage_name in stage_order:
+                                                if stage_name in stages_grouped.groups:
+                                                    stage_data = stages_grouped.get_group(stage_name)
+                                                    user_aggregated = stage_data.groupby('User')['Time spent (s)'].sum().reset_index()
+                                                    for idx, user_task in user_aggregated.iterrows():
+                                                        user_name = user_task['User']
+                                                        user_display = user_name if user_name and user_name != "Not set" else "Unassigned"
+                                                        current_stages_with_users.append(f"{stage_name} ({user_display})")
+                                            
+                                            if current_stages_with_users:
+                                                selected_remove_stage = st.selectbox(
+                                                    "Remove stage:",
+                                                    options=["Select stage to remove..."] + current_stages_with_users,
+                                                    key=f"remove_stage_select_{book_title}"
+                                                )
+                                                
+                                                if selected_remove_stage != "Select stage to remove...":
+                                                    # Parse the selection to get stage name and user
+                                                    stage_user_match = selected_remove_stage.split(" (")
+                                                    remove_stage_name = stage_user_match[0]
+                                                    remove_user_name = stage_user_match[1].rstrip(")")
+                                                    if remove_user_name == "Unassigned":
+                                                        remove_user_name = "Not set"
+                                                    
+                                                    if st.button("Remove", key=f"remove_confirm_{book_title}_{remove_stage_name}_{remove_user_name}", type="secondary"):
+                                                        if delete_task_stage(engine, book_title, remove_user_name, remove_stage_name):
+                                                            st.success(f"Removed {remove_stage_name} for {remove_user_name}")
+                                                            # Manual time added successfully
+                                                        else:
+                                                            st.error("Failed to remove stage")
+                                    
+                                    # Archive and Delete buttons at the bottom of each book
+                                    st.markdown("---")
+                                    col1, col2 = st.columns(2)
+                                    
+                                    with col1:
+                                        if st.button(f"Archive '{book_title}'", key=f"archive_{book_title}", help="Move this book to archive"):
+                                            try:
+                                                with engine.connect() as conn:
+                                                    # Check if book has time tracking records
+                                                    result = conn.execute(text('''
+                                                        SELECT COUNT(*) FROM trello_time_tracking 
+                                                        WHERE card_name = :card_name
+                                                    '''), {'card_name': book_title})
+                                                    record_count = result.scalar()
+                                                    
+                                                    if record_count > 0:
+                                                        # Archive existing time tracking records
+                                                        conn.execute(text('''
+                                                            UPDATE trello_time_tracking 
+                                                            SET archived = TRUE 
+                                                            WHERE card_name = :card_name
+                                                        '''), {'card_name': book_title})
+                                                    else:
+                                                        # Create a placeholder archived record for books without tasks
+                                                        conn.execute(text('''
+                                                            INSERT INTO trello_time_tracking 
+                                                            (card_name, user_name, list_name, time_spent_seconds, 
+                                                             card_estimate_seconds, board_name, archived, created_at)
+                                                            VALUES (:card_name, 'Not set', 'No tasks assigned', 0, 
+                                                                   0, 'Manual Entry', TRUE, NOW())
+                                                        '''), {'card_name': book_title})
+                                                    
+                                                    # Archive the book in books table
+                                                    conn.execute(text('''
+                                                        UPDATE books 
+                                                        SET archived = TRUE 
+                                                        WHERE card_name = :book_name
+                                                    '''), {'book_name': book_title})
+                                                    
+                                                    conn.commit()
+                                                
+                                                # Keep user on the current tab
+                                                st.session_state.active_tab = 0  # Book Progress tab
+                                                st.success(f"'{book_title}' has been archived successfully!")
+                                                # Archive operation completed
+                                            except Exception as e:
+                                                st.error(f"Error archiving book: {str(e)}")
+                                    
+                                    with col2:
+                                        if st.button(f"Delete '{book_title}'", key=f"delete_progress_{book_title}", help="Permanently delete this book and all its data", type="secondary"):
+                                            # Add confirmation using session state
+                                            confirm_key = f"confirm_delete_progress_{book_title}"
+                                            if confirm_key not in st.session_state:
+                                                st.session_state[confirm_key] = False
+                                            
+                                            if not st.session_state[confirm_key]:
+                                                st.session_state[confirm_key] = True
+                                                st.warning(f"Click 'Delete {book_title}' again to permanently delete all data for this book.")
+                                            else:
+                                                try:
+                                                    with engine.connect() as conn:
+                                                        conn.execute(text('''
+                                                            DELETE FROM trello_time_tracking 
+                                                            WHERE card_name = :card_name
+                                                        '''), {'card_name': book_title})
+                                                        conn.commit()
+                                                    
+                                                    # Reset confirmation state
+                                                    del st.session_state[confirm_key]
+                                                    # Keep user on the Book Progress tab
+                                                    st.session_state.active_tab = 0  # Book Progress tab
+                                                    st.success(f"'{book_title}' has been permanently deleted!")
+                                                    # Delete operation completed
+                                                except Exception as e:
+                                                    st.error(f"Error deleting book: {str(e)}")
+                                                    # Reset confirmation state on error
+                                                    if confirm_key in st.session_state:
+                                                        del st.session_state[confirm_key]
+                                
+                                stage_counter += 1
+        
+        except Exception as e:
+            st.error(f"Error accessing database: {str(e)}")
+            # Add simplified debug info
+            try:
+                import traceback
+                error_details = traceback.format_exc().split('\n')[-3:-1]  # Get last 2 lines
+                st.error(f"Location: {' '.join(error_details)}")
+            except:
+                pass  # Ignore debug errors
+        
+        # Add table showing all books with their boards below the book cards
+        st.markdown("---")
+        st.subheader("All Books Overview")
+        
+        # Create data for the table
+        table_data = []
+        
+        # Create a dictionary to track books and their boards
+        book_board_map = {}
+        
+        # First, add books with tasks from database
+        if df_from_db is not None and not df_from_db.empty and 'Card name' in df_from_db.columns:
+            try:
+                for _, row in df_from_db.groupby('Card name').first().iterrows():
+                    book_name = row['Card name']
+                    board_name = row['Board'] if 'Board' in row and row['Board'] else 'Not set'
+                    book_board_map[book_name] = board_name
+            except Exception as e:
+                # If groupby fails, fall back to simple iteration
+                pass
+        
+        # Then add books without tasks from all_books
+        try:
+            for book_info in all_books:
+                book_name = book_info[0]
+                if book_name not in book_board_map:
+                    board_name = book_info[1] if book_info[1] else 'Not set'
+                    book_board_map[book_name] = board_name
+        except Exception as e:
+            # Handle case where all_books might be empty or malformed
+            pass
+        
+        # Convert to sorted list for table display
+        for book_name in sorted(book_board_map.keys()):
+            table_data.append({
+                'Book Name': book_name,
+                'Board': book_board_map[book_name]
+            })
+        
+        if table_data:
+            # Create DataFrame for display (pd is already imported at top of file)
+            table_df = pd.DataFrame(table_data)
+            
+            # Display the table
+            st.dataframe(
+                table_df,
+                use_container_width=True,
+                hide_index=True
+            )
+        else:
+            st.info("No books found in the database.")
+        
+        # Clear refresh flags without automatic rerun to prevent infinite loops
+        for flag in ['completion_changed', 'major_update_needed']:
+            if flag in st.session_state:
+                del st.session_state[flag]
+    
+    elif selected_tab == "Reporting":
+        st.header("Reporting")
+        st.markdown("Filter tasks by user, book, board, tag, and date range from all uploaded data.")
+        
+        # Get filter options from database
+        users = get_users_from_database(engine)
+        books = get_books_from_database(engine)
+        boards = get_boards_from_database(engine)
+        tags = get_tags_from_database(engine)
+        
+        if not users:
+            st.info("No users found in database. Please add entries in the 'Add Book' tab first.")
+            return
+        
+        # Filter selection - organized in columns
+        col1, col2 = st.columns(2)
+        
+        with col1:
+            # User selection dropdown
+            selected_user = st.selectbox(
+                "Select User:",
+                options=["All Users"] + users,
+                help="Choose a user to view their tasks"
+            )
+            
+            # Book search input
+            book_search = st.text_input(
+                "Search Book (optional):",
+                placeholder="Start typing to search books...",
+                help="Type to search for a specific book"
+            )
+            # Match the search to available books
+            if book_search:
+                matched_books = [book for book in books if book_search.lower() in book.lower()]
+                if matched_books:
+                    selected_book = st.selectbox(
+                        "Select from matches:",
+                        options=matched_books,
+                        help="Choose from matching books"
+                    )
+                else:
+                    st.warning("No books found matching your search")
+                    selected_book = "All Books"
+            else:
+                selected_book = "All Books"
+        
+        with col2:
+            # Board selection dropdown
+            selected_board = st.selectbox(
+                "Select Board (optional):",
+                options=["All Boards"] + boards,
+                help="Choose a specific board to filter by"
+            )
+            
+            # Tag selection dropdown
+            selected_tag = st.selectbox(
+                "Select Tag (optional):",
+                options=["All Tags"] + tags,
+                help="Choose a specific tag to filter by"
+            )
+        
+        # Date range selection
+        col1, col2 = st.columns(2)
+        with col1:
+            start_date = st.date_input(
+                "Start Date (optional):",
+                value=None,
+                help="Leave empty to include all dates"
+            )
+        
+        with col2:
+            end_date = st.date_input(
+                "End Date (optional):",
+                value=None,
+                help="Leave empty to include all dates"
+            )
+        
+        # Update button
+        update_button = st.button("Update Table", type="primary")
+        
+        # Validate date range
+        if start_date and end_date and start_date > end_date:
+            st.error("Start date must be before end date")
+            return
+        
+        # Filter and display results only when button is clicked or on initial load
+        if update_button or 'filtered_tasks_displayed' not in st.session_state:
+            with st.spinner("Loading filtered tasks..."):
+                filtered_tasks = get_filtered_tasks_from_database(
+                    engine, 
+                    user_name=selected_user if selected_user != "All Users" else None,
+                    book_name=selected_book if selected_book != "All Books" else None,
+                    board_name=selected_board if selected_board != "All Boards" else None,
+                    tag_name=selected_tag if selected_tag != "All Tags" else None,
+                    start_date=start_date, 
+                    end_date=end_date
+                )
+            
+            # Store in session state to prevent automatic reloading
+            st.session_state.filtered_tasks_displayed = True
+            st.session_state.current_filtered_tasks = filtered_tasks
+            st.session_state.current_filters = {
+                'user': selected_user,
+                'book': selected_book,
+                'board': selected_board,
+                'tag': selected_tag,
+                'start_date': start_date,
+                'end_date': end_date
+            }
+        
+        # Display cached results if available
+        if 'current_filtered_tasks' in st.session_state:
+            
+            filtered_tasks = st.session_state.current_filtered_tasks
+            current_filters = st.session_state.get('current_filters', {})
+            
+            if not filtered_tasks.empty:
+                st.subheader("Filtered Results")
+                
+                # Show active filters info
+                active_filters = []
+                if current_filters.get('user') and current_filters.get('user') != "All Users":
+                    active_filters.append(f"User: {current_filters.get('user')}")
+                if current_filters.get('book') and current_filters.get('book') != "All Books":
+                    active_filters.append(f"Book: {current_filters.get('book')}")
+                if current_filters.get('board') and current_filters.get('board') != "All Boards":
+                    active_filters.append(f"Board: {current_filters.get('board')}")
+                if current_filters.get('tag') and current_filters.get('tag') != "All Tags":
+                    active_filters.append(f"Tag: {current_filters.get('tag')}")
+                if current_filters.get('start_date') or current_filters.get('end_date'):
+                    start_str = current_filters.get('start_date').strftime('%d/%m/%Y') if current_filters.get('start_date') else 'All'
+                    end_str = current_filters.get('end_date').strftime('%d/%m/%Y') if current_filters.get('end_date') else 'All'
+                    active_filters.append(f"Date range: {start_str} to {end_str}")
+                
+                if active_filters:
+                    st.info("Active filters: " + " | ".join(active_filters))
+                
+                st.dataframe(
+                    filtered_tasks,
+                    use_container_width=True,
+                    hide_index=True
+                )
+                
+                # Download button for filtered results
+                csv_buffer = io.StringIO()
+                filtered_tasks.to_csv(csv_buffer, index=False)
+                st.download_button(
+                    label="Download Filtered Results",
+                    data=csv_buffer.getvalue(),
+                    file_name="filtered_tasks.csv",
+                    mime="text/csv"
+                )
+                
+                # Summary statistics for filtered data
+                st.subheader("Summary")
+                col1, col2, col3, col4 = st.columns(4)
+                
+                with col1:
+                    st.metric("Total Books", int(filtered_tasks['Book Title'].nunique()))
+                
+                with col2:
+                    st.metric("Total Tasks", len(filtered_tasks))
+                
+                with col3:
+                    st.metric("Unique Users", int(filtered_tasks['User'].nunique()))
+                
+                with col4:
+                    # Calculate total time from formatted time strings
+                    total_seconds = 0
+                    for time_str in filtered_tasks['Time Spent']:
+                        if time_str != "00:00:00":
+                            parts = time_str.split(':')
+                            total_seconds += int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
+                    total_hours = total_seconds / 3600
+                    st.metric("Total Time (Hours)", f"{total_hours:.1f}")
+            
+            else:
+                st.warning("No tasks found matching the selected filters.")
+        
+        elif 'filtered_tasks_displayed' not in st.session_state:
+            st.info("Click 'Update Table' to load filtered results.")
+    
+    elif selected_tab == "Archive":
+        st.header("Archive")
+        st.markdown("View and manage archived books.")
+        
+        try:
+            # Get count of archived records
+            with engine.connect() as conn:
+                archived_count = conn.execute(text('SELECT COUNT(*) FROM trello_time_tracking WHERE archived = TRUE')).scalar()
             
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
diff --git a/attached_assets/Pasted-Columns-Card-name-Card-link-Card-estimate-Card-estimate-s-Card-estimate-h-Card-e-1751440349192_1751440349193.txt b/attached_assets/Pasted-Columns-Card-name-Card-link-Card-estimate-Card-estimate-s-Card-estimate-h-Card-e-1751440349192_1751440349193.txt
deleted file mode 100644
index 76474db582b778aae48e6ccd3650d3568fde7b2f..0000000000000000000000000000000000000000
--- a/attached_assets/Pasted-Columns-Card-name-Card-link-Card-estimate-Card-estimate-s-Card-estimate-h-Card-e-1751440349192_1751440349193.txt
+++ /dev/null
@@ -1,5 +0,0 @@
-Columns: ['Card name', 'Card link', 'Card estimate', 'Card estimate(s)', 'Card estimate(h)', 'Card estimate(f)', 'Board', 'List', 'User', 'Comment', 'Labels', 'Date started', 'Date started (f)', 'Time spent', 'Time spent (s)', 'Time spent (h)', 'Time spent (f)']
-
-Error processing book summary: attempt to get argmax of an empty sequence
-
-Full error details: Traceback (most recent call last): File "/home/runner/workspace/app.py", line 248, in process_book_summary most_recent_list = get_most_recent_activity(original_df, book_title) ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ File "/home/runner/workspace/app.py", line 181, in get_most_recent_activity most_recent = card_data_with_dates.loc[card_data_with_dates['parsed_date'].idxmax()] ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/pandas/core/series.py", line 2770, in idxmax i = self.argmax(axis, skipna, *args, **kwargs) ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/pandas/core/base.py", line 753, in argmax return delegate.argmax() ^^^^^^^^^^^^^^^^^ File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/pandas/core/arrays/_mixins.py", line 221, in argmax return nargminmax(self, "argmax", axis=axis) ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/pandas/core/sorting.py", line 483, in nargminmax return _nanargminmax(arr_values, mask, func) ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/pandas/core/sorting.py", line 494, in _nanargminmax return non_nan_idx[func(non_nans)] ^^^^^^^^^^^^^^ File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/numpy/_core/fromnumeric.py", line 1341, in argmax return _wrapfunc(a, 'argmax', axis=axis, out=out, **kwds) ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/numpy/_core/fromnumeric.py", line 57, in _wrapfunc return bound(*args, **kwds) ^^^^^^^^^^^^^^^^^^^^ ValueError: attempt to get argmax of an empty sequence
\ No newline at end of file
diff --git a/attached_assets/Pasted-streamlit-runtime-caching-cache-errors-UnhashableParamError-Cannot-hash-argument-engine-of-type--1751379723831_1751379723832.txt b/attached_assets/Pasted-streamlit-runtime-caching-cache-errors-UnhashableParamError-Cannot-hash-argument-engine-of-type--1751379723831_1751379723832.txt
deleted file mode 100644
index 67e1f2d27da6124e3d982a06c44040e0fb5e12b8..0000000000000000000000000000000000000000
--- a/attached_assets/Pasted-streamlit-runtime-caching-cache-errors-UnhashableParamError-Cannot-hash-argument-engine-of-type--1751379723831_1751379723832.txt
+++ /dev/null
@@ -1,48 +0,0 @@
-streamlit.runtime.caching.cache_errors.UnhashableParamError: Cannot hash argument 'engine' (of type sqlalchemy.engine.base.Engine) in 'get_users_from_database'.
-
-To address this, you can tell Streamlit not to hash this argument by adding a leading underscore to the argument's name in the function signature:
-
-@st.cache_data
-def get_users_from_database(_engine, ...):
-    ...
-
-Traceback:
-File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/streamlit/runtime/scriptrunner/exec_code.py", line 128, in exec_func_with_error_handling
-    result = func()
-             ^^^^^^
-File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/streamlit/runtime/scriptrunner/script_runner.py", line 669, in code_to_exec
-    exec(code, module.__dict__)  # noqa: S102
-    ^^^^^^^^^^^^^^^^^^^^^^^^^^^
-File "/home/runner/workspace/app.py", line 573, in <module>
-    main()
-File "/home/runner/workspace/app.py", line 459, in main
-    users = get_users_from_database(engine)
-            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
-File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/streamlit/runtime/caching/cache_utils.py", line 219, in __call__
-    return self._get_or_create_cached_value(args, kwargs, spinner_message)
-           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
-File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/streamlit/runtime/caching/cache_utils.py", line 234, in _get_or_create_cached_value
-    value_key = _make_value_key(
-                ^^^^^^^^^^^^^^^^
-File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/streamlit/runtime/caching/cache_utils.py", line 458, in _make_value_key
-    raise UnhashableParamError(cache_type, func, arg_name, arg_value, exc)
-File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/streamlit/runtime/caching/cache_utils.py", line 450, in _make_value_key
-    update_hash(
-File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/streamlit/runtime/caching/hashing.py", line 169, in update_hash
-    ch.update(hasher, val)
-File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/streamlit/runtime/caching/hashing.py", line 345, in update
-    b = self.to_bytes(obj)
-        ^^^^^^^^^^^^^^^^^^
-File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/streamlit/runtime/caching/hashing.py", line 327, in to_bytes
-    b = b"%s:%s" % (tname, self._to_bytes(obj))
-                           ^^^^^^^^^^^^^^^^^^^
-File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/streamlit/runtime/caching/hashing.py", line 624, in _to_bytes
-    self.update(h, item)
-File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/streamlit/runtime/caching/hashing.py", line 345, in update
-    b = self.to_bytes(obj)
-        ^^^^^^^^^^^^^^^^^^
-File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/streamlit/runtime/caching/hashing.py", line 327, in to_bytes
-    b = b"%s:%s" % (tname, self._to_bytes(obj))
-                           ^^^^^^^^^^^^^^^^^^^
-File "/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages/streamlit/runtime/caching/hashing.py", line 621, in _to_bytes
-    raise UnhashableTypeError() from ex
\ No newline at end of file
diff --git a/attached_assets/content-1751440653798.md b/attached_assets/content-1751440653798.md
deleted file mode 100644
index 3fefb9b547da9169d87ba7fadd9470c603823712..0000000000000000000000000000000000000000
--- a/attached_assets/content-1751440653798.md
+++ /dev/null
@@ -1,2142 +0,0 @@
-Release: 2.0.41current release
-
-\| Release Date: May 14, 2025
-
-
-
-# [SQLAlchemy 2.0 Documentation](https://docs.sqlalchemy.org/en/20/index.html)
-
-### [SQLAlchemy 2.0 Documentation](https://docs.sqlalchemy.org/en/20/index.html)
-
-current release
-
-[Home](https://docs.sqlalchemy.org/en/20/index.html)
-\| [Download this Documentation](https://docs.sqlalchemy.org/20/sqlalchemy_20.zip)
-
-Search terms:
-
-
-### [SQLAlchemy 2.0 Documentation](https://docs.sqlalchemy.org/en/20/index.html "SQLAlchemy 2.0 Documentation")
-
-- [Overview](https://docs.sqlalchemy.org/en/20/intro.html)
-- [SQLAlchemy Unified Tutorial](https://docs.sqlalchemy.org/en/20/tutorial/index.html)
-- [SQLAlchemy ORM](https://docs.sqlalchemy.org/en/20/orm/index.html)
-- [SQLAlchemy Core](https://docs.sqlalchemy.org/en/20/core/index.html)
-- [Dialects](https://docs.sqlalchemy.org/en/20/dialects/index.html)
-- [Frequently Asked Questions](https://docs.sqlalchemy.org/en/20/faq/index.html)
-- **Error Messages** [¶](https://docs.sqlalchemy.org/en/20/errors.html#)
-  - [Connections and Transactions](https://docs.sqlalchemy.org/en/20/errors.html#connections-and-transactions)
-    - [QueuePool limit of size <x> overflow <y> reached, connection timed out, timeout <z>](https://docs.sqlalchemy.org/en/20/errors.html#queuepool-limit-of-size-x-overflow-y-reached-connection-timed-out-timeout-z)
-    - [Pool class cannot be used with asyncio engine (or vice versa)](https://docs.sqlalchemy.org/en/20/errors.html#pool-class-cannot-be-used-with-asyncio-engine-or-vice-versa)
-    - [Can’t reconnect until invalid transaction is rolled back. Please rollback() fully before proceeding](https://docs.sqlalchemy.org/en/20/errors.html#can-t-reconnect-until-invalid-transaction-is-rolled-back-please-rollback-fully-before-proceeding)
-  - [DBAPI Errors](https://docs.sqlalchemy.org/en/20/errors.html#dbapi-errors)
-    - [InterfaceError](https://docs.sqlalchemy.org/en/20/errors.html#interfaceerror)
-    - [DatabaseError](https://docs.sqlalchemy.org/en/20/errors.html#databaseerror)
-    - [DataError](https://docs.sqlalchemy.org/en/20/errors.html#dataerror)
-    - [OperationalError](https://docs.sqlalchemy.org/en/20/errors.html#operationalerror)
-    - [IntegrityError](https://docs.sqlalchemy.org/en/20/errors.html#integrityerror)
-    - [InternalError](https://docs.sqlalchemy.org/en/20/errors.html#internalerror)
-    - [ProgrammingError](https://docs.sqlalchemy.org/en/20/errors.html#programmingerror)
-    - [NotSupportedError](https://docs.sqlalchemy.org/en/20/errors.html#notsupportederror)
-  - [SQL Expression Language](https://docs.sqlalchemy.org/en/20/errors.html#sql-expression-language)
-    - [Object will not produce a cache key, Performance Implications](https://docs.sqlalchemy.org/en/20/errors.html#object-will-not-produce-a-cache-key-performance-implications)
-      - [Caching disables itself if there’s any doubt](https://docs.sqlalchemy.org/en/20/errors.html#caching-disables-itself-if-there-s-any-doubt)
-      - [Assertion attributes for caching](https://docs.sqlalchemy.org/en/20/errors.html#assertion-attributes-for-caching)
-    - [Compiler StrSQLCompiler can’t render element of type <element type>](https://docs.sqlalchemy.org/en/20/errors.html#compiler-strsqlcompiler-can-t-render-element-of-type-element-type)
-    - [TypeError: <operator> not supported between instances of ‘ColumnProperty’ and <something>](https://docs.sqlalchemy.org/en/20/errors.html#typeerror-operator-not-supported-between-instances-of-columnproperty-and-something)
-    - [A value is required for bind parameter <x> (in parameter group <y>)](https://docs.sqlalchemy.org/en/20/errors.html#a-value-is-required-for-bind-parameter-x-in-parameter-group-y)
-    - [Expected FROM clause, got Select. To create a FROM clause, use the .subquery() method](https://docs.sqlalchemy.org/en/20/errors.html#expected-from-clause-got-select-to-create-a-from-clause-use-the-subquery-method)
-    - [An alias is being generated automatically for raw clauseelement](https://docs.sqlalchemy.org/en/20/errors.html#an-alias-is-being-generated-automatically-for-raw-clauseelement)
-    - [An alias is being generated automatically due to overlapping tables](https://docs.sqlalchemy.org/en/20/errors.html#an-alias-is-being-generated-automatically-due-to-overlapping-tables)
-  - [Object Relational Mapping](https://docs.sqlalchemy.org/en/20/errors.html#object-relational-mapping)
-    - [IllegalStateChangeError and concurrency exceptions](https://docs.sqlalchemy.org/en/20/errors.html#illegalstatechangeerror-and-concurrency-exceptions)
-    - [Parent instance <x> is not bound to a Session; (lazy load/deferred load/refresh/etc.) operation cannot proceed](https://docs.sqlalchemy.org/en/20/errors.html#parent-instance-x-is-not-bound-to-a-session-lazy-load-deferred-load-refresh-etc-operation-cannot-proceed)
-    - [This Session’s transaction has been rolled back due to a previous exception during flush](https://docs.sqlalchemy.org/en/20/errors.html#this-session-s-transaction-has-been-rolled-back-due-to-a-previous-exception-during-flush)
-    - [For relationship <relationship>, delete-orphan cascade is normally configured only on the “one” side of a one-to-many relationship, and not on the “many” side of a many-to-one or many-to-many relationship.](https://docs.sqlalchemy.org/en/20/errors.html#for-relationship-relationship-delete-orphan-cascade-is-normally-configured-only-on-the-one-side-of-a-one-to-many-relationship-and-not-on-the-many-side-of-a-many-to-one-or-many-to-many-relationship)
-    - [Instance <instance> is already associated with an instance of <instance> via its <attribute> attribute, and is only allowed a single parent.](https://docs.sqlalchemy.org/en/20/errors.html#instance-instance-is-already-associated-with-an-instance-of-instance-via-its-attribute-attribute-and-is-only-allowed-a-single-parent)
-    - [relationship X will copy column Q to column P, which conflicts with relationship(s): ‘Y’](https://docs.sqlalchemy.org/en/20/errors.html#relationship-x-will-copy-column-q-to-column-p-which-conflicts-with-relationship-s-y)
-    - [Object cannot be converted to ‘persistent’ state, as this identity map is no longer valid.](https://docs.sqlalchemy.org/en/20/errors.html#object-cannot-be-converted-to-persistent-state-as-this-identity-map-is-no-longer-valid)
-    - [Type annotation can’t be interpreted for Annotated Declarative Table form](https://docs.sqlalchemy.org/en/20/errors.html#type-annotation-can-t-be-interpreted-for-annotated-declarative-table-form)
-    - [When transforming <cls> to a dataclass, attribute(s) originate from superclass <cls> which is not a dataclass.](https://docs.sqlalchemy.org/en/20/errors.html#when-transforming-cls-to-a-dataclass-attribute-s-originate-from-superclass-cls-which-is-not-a-dataclass)
-    - [Python dataclasses error encountered when creating dataclass for <classname>](https://docs.sqlalchemy.org/en/20/errors.html#python-dataclasses-error-encountered-when-creating-dataclass-for-classname)
-    - [per-row ORM Bulk Update by Primary Key requires that records contain primary key values](https://docs.sqlalchemy.org/en/20/errors.html#per-row-orm-bulk-update-by-primary-key-requires-that-records-contain-primary-key-values)
-  - [AsyncIO Exceptions](https://docs.sqlalchemy.org/en/20/errors.html#asyncio-exceptions)
-    - [AwaitRequired](https://docs.sqlalchemy.org/en/20/errors.html#awaitrequired)
-    - [MissingGreenlet](https://docs.sqlalchemy.org/en/20/errors.html#missinggreenlet)
-    - [No Inspection Available](https://docs.sqlalchemy.org/en/20/errors.html#no-inspection-available)
-  - [Core Exception Classes](https://docs.sqlalchemy.org/en/20/errors.html#core-exception-classes)
-  - [ORM Exception Classes](https://docs.sqlalchemy.org/en/20/errors.html#orm-exception-classes)
-  - [Legacy Exceptions](https://docs.sqlalchemy.org/en/20/errors.html#legacy-exceptions)
-    - [The <some function> in SQLAlchemy 2.0 will no longer <something>](https://docs.sqlalchemy.org/en/20/errors.html#the-some-function-in-sqlalchemy-2-0-will-no-longer-something)
-    - [Object is being merged into a Session along the backref cascade](https://docs.sqlalchemy.org/en/20/errors.html#object-is-being-merged-into-a-session-along-the-backref-cascade)
-    - [select() construct created in “legacy” mode; keyword arguments, etc.](https://docs.sqlalchemy.org/en/20/errors.html#select-construct-created-in-legacy-mode-keyword-arguments-etc)
-    - [A bind was located via legacy bound metadata, but since future=True is set on this Session, this bind is ignored.](https://docs.sqlalchemy.org/en/20/errors.html#a-bind-was-located-via-legacy-bound-metadata-but-since-future-true-is-set-on-this-session-this-bind-is-ignored)
-    - [This Compiled object is not bound to any Engine or Connection](https://docs.sqlalchemy.org/en/20/errors.html#this-compiled-object-is-not-bound-to-any-engine-or-connection)
-    - [This connection is on an inactive transaction. Please rollback() fully before proceeding](https://docs.sqlalchemy.org/en/20/errors.html#this-connection-is-on-an-inactive-transaction-please-rollback-fully-before-proceeding)
-- [Changes and Migration](https://docs.sqlalchemy.org/en/20/changelog/index.html)
-
-#### Project Versions
-
-- [Version 2.1 (development)](https://docs.sqlalchemy.org/en/21/)
-- [Version 2.0](https://docs.sqlalchemy.org/en/20/)
-- [Version 1.4](https://docs.sqlalchemy.org/en/14/)
-- [Version 1.3](https://docs.sqlalchemy.org/en/13/)
-
-Search terms:
-
-
-[Home](https://docs.sqlalchemy.org/en/20/index.html)
-\| [Download this Documentation](https://docs.sqlalchemy.org/20/sqlalchemy_20.zip)
-
-- **Previous:** [Third Party Integration Issues](https://docs.sqlalchemy.org/en/20/faq/thirdparty.html "previous chapter")
-- **Next:** [Changes and Migration](https://docs.sqlalchemy.org/en/20/changelog/index.html "next chapter")
-- **Up:** [Home](https://docs.sqlalchemy.org/en/20/index.html)
-- **On this page:**
-  - [Error Messages](https://docs.sqlalchemy.org/en/20/errors.html#error-messages)
-    - [Connections and Transactions](https://docs.sqlalchemy.org/en/20/errors.html#connections-and-transactions)
-      - [QueuePool limit of size <x> overflow <y> reached, connection timed out, timeout <z>](https://docs.sqlalchemy.org/en/20/errors.html#queuepool-limit-of-size-x-overflow-y-reached-connection-timed-out-timeout-z)
-      - [Pool class cannot be used with asyncio engine (or vice versa)](https://docs.sqlalchemy.org/en/20/errors.html#pool-class-cannot-be-used-with-asyncio-engine-or-vice-versa)
-      - [Can’t reconnect until invalid transaction is rolled back. Please rollback() fully before proceeding](https://docs.sqlalchemy.org/en/20/errors.html#can-t-reconnect-until-invalid-transaction-is-rolled-back-please-rollback-fully-before-proceeding)
-    - [DBAPI Errors](https://docs.sqlalchemy.org/en/20/errors.html#dbapi-errors)
-      - [InterfaceError](https://docs.sqlalchemy.org/en/20/errors.html#interfaceerror)
-      - [DatabaseError](https://docs.sqlalchemy.org/en/20/errors.html#databaseerror)
-      - [DataError](https://docs.sqlalchemy.org/en/20/errors.html#dataerror)
-      - [OperationalError](https://docs.sqlalchemy.org/en/20/errors.html#operationalerror)
-      - [IntegrityError](https://docs.sqlalchemy.org/en/20/errors.html#integrityerror)
-      - [InternalError](https://docs.sqlalchemy.org/en/20/errors.html#internalerror)
-      - [ProgrammingError](https://docs.sqlalchemy.org/en/20/errors.html#programmingerror)
-      - [NotSupportedError](https://docs.sqlalchemy.org/en/20/errors.html#notsupportederror)
-    - [SQL Expression Language](https://docs.sqlalchemy.org/en/20/errors.html#sql-expression-language)
-      - [Object will not produce a cache key, Performance Implications](https://docs.sqlalchemy.org/en/20/errors.html#object-will-not-produce-a-cache-key-performance-implications)
-        - [Caching disables itself if there’s any doubt](https://docs.sqlalchemy.org/en/20/errors.html#caching-disables-itself-if-there-s-any-doubt)
-        - [Assertion attributes for caching](https://docs.sqlalchemy.org/en/20/errors.html#assertion-attributes-for-caching)
-      - [Compiler StrSQLCompiler can’t render element of type <element type>](https://docs.sqlalchemy.org/en/20/errors.html#compiler-strsqlcompiler-can-t-render-element-of-type-element-type)
-      - [TypeError: <operator> not supported between instances of ‘ColumnProperty’ and <something>](https://docs.sqlalchemy.org/en/20/errors.html#typeerror-operator-not-supported-between-instances-of-columnproperty-and-something)
-      - [A value is required for bind parameter <x> (in parameter group <y>)](https://docs.sqlalchemy.org/en/20/errors.html#a-value-is-required-for-bind-parameter-x-in-parameter-group-y)
-      - [Expected FROM clause, got Select. To create a FROM clause, use the .subquery() method](https://docs.sqlalchemy.org/en/20/errors.html#expected-from-clause-got-select-to-create-a-from-clause-use-the-subquery-method)
-      - [An alias is being generated automatically for raw clauseelement](https://docs.sqlalchemy.org/en/20/errors.html#an-alias-is-being-generated-automatically-for-raw-clauseelement)
-      - [An alias is being generated automatically due to overlapping tables](https://docs.sqlalchemy.org/en/20/errors.html#an-alias-is-being-generated-automatically-due-to-overlapping-tables)
-    - [Object Relational Mapping](https://docs.sqlalchemy.org/en/20/errors.html#object-relational-mapping)
-      - [IllegalStateChangeError and concurrency exceptions](https://docs.sqlalchemy.org/en/20/errors.html#illegalstatechangeerror-and-concurrency-exceptions)
-      - [Parent instance <x> is not bound to a Session; (lazy load/deferred load/refresh/etc.) operation cannot proceed](https://docs.sqlalchemy.org/en/20/errors.html#parent-instance-x-is-not-bound-to-a-session-lazy-load-deferred-load-refresh-etc-operation-cannot-proceed)
-      - [This Session’s transaction has been rolled back due to a previous exception during flush](https://docs.sqlalchemy.org/en/20/errors.html#this-session-s-transaction-has-been-rolled-back-due-to-a-previous-exception-during-flush)
-      - [For relationship <relationship>, delete-orphan cascade is normally configured only on the “one” side of a one-to-many relationship, and not on the “many” side of a many-to-one or many-to-many relationship.](https://docs.sqlalchemy.org/en/20/errors.html#for-relationship-relationship-delete-orphan-cascade-is-normally-configured-only-on-the-one-side-of-a-one-to-many-relationship-and-not-on-the-many-side-of-a-many-to-one-or-many-to-many-relationship)
-      - [Instance <instance> is already associated with an instance of <instance> via its <attribute> attribute, and is only allowed a single parent.](https://docs.sqlalchemy.org/en/20/errors.html#instance-instance-is-already-associated-with-an-instance-of-instance-via-its-attribute-attribute-and-is-only-allowed-a-single-parent)
-      - [relationship X will copy column Q to column P, which conflicts with relationship(s): ‘Y’](https://docs.sqlalchemy.org/en/20/errors.html#relationship-x-will-copy-column-q-to-column-p-which-conflicts-with-relationship-s-y)
-      - [Object cannot be converted to ‘persistent’ state, as this identity map is no longer valid.](https://docs.sqlalchemy.org/en/20/errors.html#object-cannot-be-converted-to-persistent-state-as-this-identity-map-is-no-longer-valid)
-      - [Type annotation can’t be interpreted for Annotated Declarative Table form](https://docs.sqlalchemy.org/en/20/errors.html#type-annotation-can-t-be-interpreted-for-annotated-declarative-table-form)
-      - [When transforming <cls> to a dataclass, attribute(s) originate from superclass <cls> which is not a dataclass.](https://docs.sqlalchemy.org/en/20/errors.html#when-transforming-cls-to-a-dataclass-attribute-s-originate-from-superclass-cls-which-is-not-a-dataclass)
-      - [Python dataclasses error encountered when creating dataclass for <classname>](https://docs.sqlalchemy.org/en/20/errors.html#python-dataclasses-error-encountered-when-creating-dataclass-for-classname)
-      - [per-row ORM Bulk Update by Primary Key requires that records contain primary key values](https://docs.sqlalchemy.org/en/20/errors.html#per-row-orm-bulk-update-by-primary-key-requires-that-records-contain-primary-key-values)
-    - [AsyncIO Exceptions](https://docs.sqlalchemy.org/en/20/errors.html#asyncio-exceptions)
-      - [AwaitRequired](https://docs.sqlalchemy.org/en/20/errors.html#awaitrequired)
-      - [MissingGreenlet](https://docs.sqlalchemy.org/en/20/errors.html#missinggreenlet)
-      - [No Inspection Available](https://docs.sqlalchemy.org/en/20/errors.html#no-inspection-available)
-    - [Core Exception Classes](https://docs.sqlalchemy.org/en/20/errors.html#core-exception-classes)
-    - [ORM Exception Classes](https://docs.sqlalchemy.org/en/20/errors.html#orm-exception-classes)
-    - [Legacy Exceptions](https://docs.sqlalchemy.org/en/20/errors.html#legacy-exceptions)
-      - [The <some function> in SQLAlchemy 2.0 will no longer <something>](https://docs.sqlalchemy.org/en/20/errors.html#the-some-function-in-sqlalchemy-2-0-will-no-longer-something)
-      - [Object is being merged into a Session along the backref cascade](https://docs.sqlalchemy.org/en/20/errors.html#object-is-being-merged-into-a-session-along-the-backref-cascade)
-      - [select() construct created in “legacy” mode; keyword arguments, etc.](https://docs.sqlalchemy.org/en/20/errors.html#select-construct-created-in-legacy-mode-keyword-arguments-etc)
-      - [A bind was located via legacy bound metadata, but since future=True is set on this Session, this bind is ignored.](https://docs.sqlalchemy.org/en/20/errors.html#a-bind-was-located-via-legacy-bound-metadata-but-since-future-true-is-set-on-this-session-this-bind-is-ignored)
-      - [This Compiled object is not bound to any Engine or Connection](https://docs.sqlalchemy.org/en/20/errors.html#this-compiled-object-is-not-bound-to-any-engine-or-connection)
-      - [This connection is on an inactive transaction. Please rollback() fully before proceeding](https://docs.sqlalchemy.org/en/20/errors.html#this-connection-is-on-an-inactive-transaction-please-rollback-fully-before-proceeding)
-
-# Error Messages [¶](https://docs.sqlalchemy.org/en/20/errors.html\#error-messages "Link to this heading")
-
-This section lists descriptions and background for common error messages
-and warnings raised or emitted by SQLAlchemy.
-
-SQLAlchemy normally raises errors within the context of a SQLAlchemy-specific
-exception class. For details on these classes, see
-[Core Exceptions](https://docs.sqlalchemy.org/en/20/core/exceptions.html) and [ORM Exceptions](https://docs.sqlalchemy.org/en/20/orm/exceptions.html).
-
-SQLAlchemy errors can roughly be separated into two categories, the
-**programming-time error** and the **runtime error**. Programming-time
-errors are raised as a result of functions or methods being called with
-incorrect arguments, or from other configuration-oriented methods such as
-mapper configurations that can’t be resolved. The programming-time error is
-typically immediate and deterministic. The runtime error on the other hand
-represents a failure that occurs as a program runs in response to some
-condition that occurs arbitrarily, such as database connections being
-exhausted or some data-related issue occurring. Runtime errors are more
-likely to be seen in the logs of a running application as the program
-encounters these states in response to load and data being encountered.
-
-Since runtime errors are not as easy to reproduce and often occur in response
-to some arbitrary condition as the program runs, they are more difficult to
-debug and also affect programs that have already been put into production.
-
-Within this section, the goal is to try to provide background on some of the
-most common runtime errors as well as programming time errors.
-
-## Connections and Transactions [¶](https://docs.sqlalchemy.org/en/20/errors.html\#connections-and-transactions "Link to this heading")
-
-### QueuePool limit of size <x> overflow <y> reached, connection timed out, timeout <z> [¶](https://docs.sqlalchemy.org/en/20/errors.html\#queuepool-limit-of-size-x-overflow-y-reached-connection-timed-out-timeout-z "Link to this heading")
-
-This is possibly the most common runtime error experienced, as it directly
-involves the work load of the application surpassing a configured limit, one
-which typically applies to nearly all SQLAlchemy applications.
-
-The following points summarize what this error means, beginning with the
-most fundamental points that most SQLAlchemy users should already be
-familiar with.
-
-- **The SQLAlchemy Engine object uses a pool of connections by default** \- What
-this means is that when one makes use of a SQL database connection resource
-of an [`Engine`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Engine "sqlalchemy.engine.Engine") object, and then [releases](https://docs.sqlalchemy.org/en/20/glossary.html#term-releases) that resource,
-the database connection itself remains connected to the database and
-is returned to an internal queue where it can be used again. Even though
-the code may appear to be ending its conversation with the database, in many
-cases the application will still maintain a fixed number of database connections
-that persist until the application ends or the pool is explicitly disposed.
-
-- Because of the pool, when an application makes use of a SQL database
-connection, most typically from either making use of [`Engine.connect()`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Engine.connect "sqlalchemy.engine.Engine.connect")
-or when making queries using an ORM [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session"), this activity
-does not necessarily establish a new connection to the database at the
-moment the connection object is acquired; it instead consults the
-connection pool for a connection, which will often retrieve an existing
-connection from the pool to be re-used. If no connections are available,
-the pool will create a new database connection, but only if the
-pool has not surpassed a configured capacity.
-
-- The default pool used in most cases is called [`QueuePool`](https://docs.sqlalchemy.org/en/20/core/pooling.html#sqlalchemy.pool.QueuePool "sqlalchemy.pool.QueuePool"). When
-you ask this pool to give you a connection and none are available, it
-will create a new connection **if the total number of connections in play**
-**are less than a configured value**. This value is equal to the
-**pool size plus the max overflow**. That means if you have configured
-your engine as:
-
-
-
-
-
-```
-engine = create_engine("mysql+mysqldb://u:p@host/db", pool_size=10, max_overflow=20)
-```
-
-Copy to clipboard
-
-
-
-The above [`Engine`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Engine "sqlalchemy.engine.Engine") will allow **at most 30 connections** to be in
-play at any time, not including connections that were detached from the
-engine or invalidated. If a request for a new connection arrives and
-30 connections are already in use by other parts of the application,
-the connection pool will block for a fixed period of time,
-before timing out and raising this error message.
-
-In order to allow for a higher number of connections be in use at once,
-the pool can be adjusted using the
-[`create_engine.pool_size`](https://docs.sqlalchemy.org/en/20/core/engines.html#sqlalchemy.create_engine.params.pool_size "sqlalchemy.create_engine") and [`create_engine.max_overflow`](https://docs.sqlalchemy.org/en/20/core/engines.html#sqlalchemy.create_engine.params.max_overflow "sqlalchemy.create_engine")
-parameters as passed to the [`create_engine()`](https://docs.sqlalchemy.org/en/20/core/engines.html#sqlalchemy.create_engine "sqlalchemy.create_engine") function. The timeout
-to wait for a connection to be available is configured using the
-[`create_engine.pool_timeout`](https://docs.sqlalchemy.org/en/20/core/engines.html#sqlalchemy.create_engine.params.pool_timeout "sqlalchemy.create_engine") parameter.
-
-- The pool can be configured to have unlimited overflow by setting
-[`create_engine.max_overflow`](https://docs.sqlalchemy.org/en/20/core/engines.html#sqlalchemy.create_engine.params.max_overflow "sqlalchemy.create_engine") to the value “-1”. With this setting,
-the pool will still maintain a fixed pool of connections, however it will
-never block upon a new connection being requested; it will instead unconditionally
-make a new connection if none are available.
-
-However, when running in this way, if the application has an issue where it
-is using up all available connectivity resources, it will eventually hit the
-configured limit of available connections on the database itself, which will
-again return an error. More seriously, when the application exhausts the
-database of connections, it usually will have caused a great
-amount of resources to be used up before failing, and can also interfere
-with other applications and database status mechanisms that rely upon being
-able to connect to the database.
-
-Given the above, the connection pool can be looked at as a **safety valve**
-**for connection use**, providing a critical layer of protection against
-a rogue application causing the entire database to become unavailable
-to all other applications. When receiving this error message, it is vastly
-preferable to repair the issue using up too many connections and/or
-configure the limits appropriately, rather than allowing for unlimited
-overflow which does not actually solve the underlying issue.
-
-
-What causes an application to use up all the connections that it has available?
-
-- **The application is fielding too many concurrent requests to do work based**
-**on the configured value for the pool** \- This is the most straightforward
-cause. If you have
-an application that runs in a thread pool that allows for 30 concurrent
-threads, with one connection in use per thread, if your pool is not configured
-to allow at least 30 connections checked out at once, you will get this
-error once your application receives enough concurrent requests. Solution
-is to raise the limits on the pool or lower the number of concurrent threads.
-
-- **The application is not returning connections to the pool** \- This is the
-next most common reason, which is that the application is making use of the
-connection pool, but the program is failing to [release](https://docs.sqlalchemy.org/en/20/glossary.html#term-release) these
-connections and is instead leaving them open. The connection pool as well
-as the ORM [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session") do have logic such that when the session and/or
-connection object is garbage collected, it results in the underlying
-connection resources being released, however this behavior cannot be relied
-upon to release resources in a timely manner.
-
-A common reason this can occur is that the application uses ORM sessions and
-does not call [`Session.close()`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session.close "sqlalchemy.orm.Session.close") upon them once the work involving that
-session is complete. Solution is to make sure ORM sessions if using the ORM,
-or engine-bound [`Connection`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Connection "sqlalchemy.engine.Connection") objects if using Core, are explicitly
-closed at the end of the work being done, either via the appropriate
-`.close()` method, or by using one of the available context managers (e.g.
-“with:” statement) to properly release the resource.
-
-- **The application is attempting to run long-running transactions** \- A
-database transaction is a very expensive resource, and should **never be**
-**left idle waiting for some event to occur**. If an application is waiting
-for a user to push a button, or a result to come off of a long running job
-queue, or is holding a persistent connection open to a browser, **don’t**
-**keep a database transaction open for the whole time**. As the application
-needs to work with the database and interact with an event, open a short-lived
-transaction at that point and then close it.
-
-- **The application is deadlocking** \- Also a common cause of this error and
-more difficult to grasp, if an application is not able to complete its use
-of a connection either due to an application-side or database-side deadlock,
-the application can use up all the available connections which then leads to
-additional requests receiving this error. Reasons for deadlocks include:
-
-  - Using an implicit async system such as gevent or eventlet without
-    properly monkeypatching all socket libraries and drivers, or which
-    has bugs in not fully covering for all monkeypatched driver methods,
-    or less commonly when the async system is being used against CPU-bound
-    workloads and greenlets making use of database resources are simply waiting
-    too long to attend to them. Neither implicit nor explicit async
-    programming frameworks are typically
-    necessary or appropriate for the vast majority of relational database
-    operations; if an application must use an async system for some area
-    of functionality, it’s best that database-oriented business methods
-    run within traditional threads that pass messages to the async part
-    of the application.
-
-  - A database side deadlock, e.g. rows are mutually deadlocked
-
-  - Threading errors, such as mutexes in a mutual deadlock, or calling
-    upon an already locked mutex in the same thread
-
-Keep in mind an alternative to using pooling is to turn off pooling entirely.
-See the section [Switching Pool Implementations](https://docs.sqlalchemy.org/en/20/core/pooling.html#pool-switching) for background on this. However, note
-that when this error message is occurring, it is **always** due to a bigger
-problem in the application itself; the pool just helps to reveal the problem
-sooner.
-
-See also
-
-[Connection Pooling](https://docs.sqlalchemy.org/en/20/core/pooling.html)
-
-[Working with Engines and Connections](https://docs.sqlalchemy.org/en/20/core/connections.html)
-
-### Pool class cannot be used with asyncio engine (or vice versa) [¶](https://docs.sqlalchemy.org/en/20/errors.html\#pool-class-cannot-be-used-with-asyncio-engine-or-vice-versa "Link to this heading")
-
-The [`QueuePool`](https://docs.sqlalchemy.org/en/20/core/pooling.html#sqlalchemy.pool.QueuePool "sqlalchemy.pool.QueuePool") pool class uses a `thread.Lock` object internally
-and is not compatible with asyncio. If using the [`create_async_engine()`](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.create_async_engine "sqlalchemy.ext.asyncio.create_async_engine")
-function to create an [`AsyncEngine`](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncEngine "sqlalchemy.ext.asyncio.AsyncEngine"), the appropriate queue pool class
-is [`AsyncAdaptedQueuePool`](https://docs.sqlalchemy.org/en/20/core/pooling.html#sqlalchemy.pool.AsyncAdaptedQueuePool "sqlalchemy.pool.AsyncAdaptedQueuePool"), which is used automatically and does
-not need to be specified.
-
-In addition to [`AsyncAdaptedQueuePool`](https://docs.sqlalchemy.org/en/20/core/pooling.html#sqlalchemy.pool.AsyncAdaptedQueuePool "sqlalchemy.pool.AsyncAdaptedQueuePool"), the [`NullPool`](https://docs.sqlalchemy.org/en/20/core/pooling.html#sqlalchemy.pool.NullPool "sqlalchemy.pool.NullPool")
-and [`StaticPool`](https://docs.sqlalchemy.org/en/20/core/pooling.html#sqlalchemy.pool.StaticPool "sqlalchemy.pool.StaticPool") pool classes do not use locks and are also
-suitable for use with async engines.
-
-This error is also raised in reverse in the unlikely case that the
-[`AsyncAdaptedQueuePool`](https://docs.sqlalchemy.org/en/20/core/pooling.html#sqlalchemy.pool.AsyncAdaptedQueuePool "sqlalchemy.pool.AsyncAdaptedQueuePool") pool class is indicated explicitly with
-the [`create_engine()`](https://docs.sqlalchemy.org/en/20/core/engines.html#sqlalchemy.create_engine "sqlalchemy.create_engine") function.
-
-See also
-
-[Connection Pooling](https://docs.sqlalchemy.org/en/20/core/pooling.html)
-
-### Can’t reconnect until invalid transaction is rolled back. Please rollback() fully before proceeding [¶](https://docs.sqlalchemy.org/en/20/errors.html\#can-t-reconnect-until-invalid-transaction-is-rolled-back-please-rollback-fully-before-proceeding "Link to this heading")
-
-This error condition refers to the case where a [`Connection`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Connection "sqlalchemy.engine.Connection") was
-invalidated, either due to a database disconnect detection or due to an
-explicit call to [`Connection.invalidate()`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Connection.invalidate "sqlalchemy.engine.Connection.invalidate"), but there is still a
-transaction present that was initiated either explicitly by the [`Connection.begin()`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Connection.begin "sqlalchemy.engine.Connection.begin")
-method, or due to the connection automatically beginning a transaction as occurs
-in the 2.x series of SQLAlchemy when any SQL statements are emitted. When a connection is invalidated, any [`Transaction`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Transaction "sqlalchemy.engine.Transaction")
-that was in progress is now in an invalid state, and must be explicitly rolled
-back in order to remove it from the [`Connection`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Connection "sqlalchemy.engine.Connection").
-
-## DBAPI Errors [¶](https://docs.sqlalchemy.org/en/20/errors.html\#dbapi-errors "Link to this heading")
-
-The Python database API, or DBAPI, is a specification for database drivers
-which can be located at [Pep-249](https://www.python.org/dev/peps/pep-0249/).
-This API specifies a set of exception classes that accommodate the full range
-of failure modes of the database.
-
-SQLAlchemy does not generate these exceptions directly. Instead, they are
-intercepted from the database driver and wrapped by the SQLAlchemy-provided
-exception [`DBAPIError`](https://docs.sqlalchemy.org/en/20/core/exceptions.html#sqlalchemy.exc.DBAPIError "sqlalchemy.exc.DBAPIError"), however the messaging within the exception is
-**generated by the driver, not SQLAlchemy**.
-
-### InterfaceError [¶](https://docs.sqlalchemy.org/en/20/errors.html\#interfaceerror "Link to this heading")
-
-Exception raised for errors that are related to the database interface rather
-than the database itself.
-
-This error is a [DBAPI Error](https://docs.sqlalchemy.org/en/20/errors.html#error-dbapi) and originates from
-the database driver (DBAPI), not SQLAlchemy itself.
-
-The `InterfaceError` is sometimes raised by drivers in the context
-of the database connection being dropped, or not being able to connect
-to the database. For tips on how to deal with this, see the section
-[Dealing with Disconnects](https://docs.sqlalchemy.org/en/20/core/pooling.html#pool-disconnects).
-
-### DatabaseError [¶](https://docs.sqlalchemy.org/en/20/errors.html\#databaseerror "Link to this heading")
-
-Exception raised for errors that are related to the database itself, and not
-the interface or data being passed.
-
-This error is a [DBAPI Error](https://docs.sqlalchemy.org/en/20/errors.html#error-dbapi) and originates from
-the database driver (DBAPI), not SQLAlchemy itself.
-
-### DataError [¶](https://docs.sqlalchemy.org/en/20/errors.html\#dataerror "Link to this heading")
-
-Exception raised for errors that are due to problems with the processed data
-like division by zero, numeric value out of range, etc.
-
-This error is a [DBAPI Error](https://docs.sqlalchemy.org/en/20/errors.html#error-dbapi) and originates from
-the database driver (DBAPI), not SQLAlchemy itself.
-
-### OperationalError [¶](https://docs.sqlalchemy.org/en/20/errors.html\#operationalerror "Link to this heading")
-
-Exception raised for errors that are related to the database’s operation and
-not necessarily under the control of the programmer, e.g. an unexpected
-disconnect occurs, the data source name is not found, a transaction could not
-be processed, a memory allocation error occurred during processing, etc.
-
-This error is a [DBAPI Error](https://docs.sqlalchemy.org/en/20/errors.html#error-dbapi) and originates from
-the database driver (DBAPI), not SQLAlchemy itself.
-
-The `OperationalError` is the most common (but not the only) error class used
-by drivers in the context of the database connection being dropped, or not
-being able to connect to the database. For tips on how to deal with this, see
-the section [Dealing with Disconnects](https://docs.sqlalchemy.org/en/20/core/pooling.html#pool-disconnects).
-
-### IntegrityError [¶](https://docs.sqlalchemy.org/en/20/errors.html\#integrityerror "Link to this heading")
-
-Exception raised when the relational integrity of the database is affected,
-e.g. a foreign key check fails.
-
-This error is a [DBAPI Error](https://docs.sqlalchemy.org/en/20/errors.html#error-dbapi) and originates from
-the database driver (DBAPI), not SQLAlchemy itself.
-
-### InternalError [¶](https://docs.sqlalchemy.org/en/20/errors.html\#internalerror "Link to this heading")
-
-Exception raised when the database encounters an internal error, e.g. the
-cursor is not valid anymore, the transaction is out of sync, etc.
-
-This error is a [DBAPI Error](https://docs.sqlalchemy.org/en/20/errors.html#error-dbapi) and originates from
-the database driver (DBAPI), not SQLAlchemy itself.
-
-The `InternalError` is sometimes raised by drivers in the context
-of the database connection being dropped, or not being able to connect
-to the database. For tips on how to deal with this, see the section
-[Dealing with Disconnects](https://docs.sqlalchemy.org/en/20/core/pooling.html#pool-disconnects).
-
-### ProgrammingError [¶](https://docs.sqlalchemy.org/en/20/errors.html\#programmingerror "Link to this heading")
-
-Exception raised for programming errors, e.g. table not found or already
-exists, syntax error in the SQL statement, wrong number of parameters
-specified, etc.
-
-This error is a [DBAPI Error](https://docs.sqlalchemy.org/en/20/errors.html#error-dbapi) and originates from
-the database driver (DBAPI), not SQLAlchemy itself.
-
-The `ProgrammingError` is sometimes raised by drivers in the context
-of the database connection being dropped, or not being able to connect
-to the database. For tips on how to deal with this, see the section
-[Dealing with Disconnects](https://docs.sqlalchemy.org/en/20/core/pooling.html#pool-disconnects).
-
-### NotSupportedError [¶](https://docs.sqlalchemy.org/en/20/errors.html\#notsupportederror "Link to this heading")
-
-Exception raised in case a method or database API was used which is not
-supported by the database, e.g. requesting a .rollback() on a connection that
-does not support transaction or has transactions turned off.
-
-This error is a [DBAPI Error](https://docs.sqlalchemy.org/en/20/errors.html#error-dbapi) and originates from
-the database driver (DBAPI), not SQLAlchemy itself.
-
-## SQL Expression Language [¶](https://docs.sqlalchemy.org/en/20/errors.html\#sql-expression-language "Link to this heading")
-
-### Object will not produce a cache key, Performance Implications [¶](https://docs.sqlalchemy.org/en/20/errors.html\#object-will-not-produce-a-cache-key-performance-implications "Link to this heading")
-
-SQLAlchemy as of version 1.4 includes a
-[SQL compilation caching facility](https://docs.sqlalchemy.org/en/20/core/connections.html#sql-caching) which will allow
-Core and ORM SQL constructs to cache their stringified form, along with other
-structural information used to fetch results from the statement, allowing the
-relatively expensive string compilation process to be skipped when another
-structurally equivalent construct is next used. This system
-relies upon functionality that is implemented for all SQL constructs, including
-objects such as [`Column`](https://docs.sqlalchemy.org/en/20/core/metadata.html#sqlalchemy.schema.Column "sqlalchemy.schema.Column"),
-[`select()`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.select "sqlalchemy.sql.expression.select"), and [`TypeEngine`](https://docs.sqlalchemy.org/en/20/core/type_api.html#sqlalchemy.types.TypeEngine "sqlalchemy.types.TypeEngine") objects, to produce a
-**cache key** which fully represents their state to the degree that it affects
-the SQL compilation process.
-
-If the warnings in question refer to widely used objects such as
-[`Column`](https://docs.sqlalchemy.org/en/20/core/metadata.html#sqlalchemy.schema.Column "sqlalchemy.schema.Column") objects, and are shown to be affecting the majority of
-SQL constructs being emitted (using the estimation techniques described at
-[Estimating Cache Performance Using Logging](https://docs.sqlalchemy.org/en/20/core/connections.html#sql-caching-logging)) such that caching is generally not enabled for an
-application, this will negatively impact performance and can in some cases
-effectively produce a **performance degradation** compared to prior SQLAlchemy
-versions. The FAQ at [Why is my application slow after upgrading to 1.4 and/or 2.x?](https://docs.sqlalchemy.org/en/20/faq/performance.html#faq-new-caching) covers this in additional detail.
-
-#### Caching disables itself if there’s any doubt [¶](https://docs.sqlalchemy.org/en/20/errors.html\#caching-disables-itself-if-there-s-any-doubt "Link to this heading")
-
-Caching relies on being able to generate a cache key that accurately represents
-the **complete structure** of a statement in a **consistent** fashion. If a particular
-SQL construct (or type) does not have the appropriate directives in place which
-allow it to generate a proper cache key, then caching cannot be safely enabled:
-
-- The cache key must represent the **complete structure**: If the usage of two
-separate instances of that construct may result in different SQL being
-rendered, caching the SQL against the first instance of the element using a
-cache key that does not capture the distinct differences between the first and
-second elements will result in incorrect SQL being cached and rendered for the
-second instance.
-
-- The cache key must be **consistent**: If a construct represents state that
-changes every time, such as a literal value, producing unique SQL for every
-instance of it, this construct is also not safe to cache, as repeated use of
-the construct will quickly fill up the statement cache with unique SQL strings
-that will likely not be used again, defeating the purpose of the cache.
-
-
-For the above two reasons, SQLAlchemy’s caching system is **extremely**
-**conservative** about deciding to cache the SQL corresponding to an object.
-
-#### Assertion attributes for caching [¶](https://docs.sqlalchemy.org/en/20/errors.html\#assertion-attributes-for-caching "Link to this heading")
-
-The warning is emitted based on the criteria below. For further detail on
-each, see the section [Why is my application slow after upgrading to 1.4 and/or 2.x?](https://docs.sqlalchemy.org/en/20/faq/performance.html#faq-new-caching).
-
-- The [`Dialect`](https://docs.sqlalchemy.org/en/20/core/internals.html#sqlalchemy.engine.Dialect "sqlalchemy.engine.Dialect") itself (i.e. the module that is specified by the
-first part of the URL we pass to [`create_engine()`](https://docs.sqlalchemy.org/en/20/core/engines.html#sqlalchemy.create_engine "sqlalchemy.create_engine"), like
-`postgresql+psycopg2://`), must indicate it has been reviewed and tested
-to support caching correctly, which is indicated by the
-[`Dialect.supports_statement_cache`](https://docs.sqlalchemy.org/en/20/core/internals.html#sqlalchemy.engine.Dialect.supports_statement_cache "sqlalchemy.engine.Dialect.supports_statement_cache") attribute being set to `True`.
-When using third party dialects, consult with the maintainers of the dialect
-so that they may follow the [steps to ensure caching may be enabled](https://docs.sqlalchemy.org/en/20/core/connections.html#engine-thirdparty-caching) in their dialect and publish a new release.
-
-- Third party or user defined types that inherit from either
-[`TypeDecorator`](https://docs.sqlalchemy.org/en/20/core/custom_types.html#sqlalchemy.types.TypeDecorator "sqlalchemy.types.TypeDecorator") or [`UserDefinedType`](https://docs.sqlalchemy.org/en/20/core/custom_types.html#sqlalchemy.types.UserDefinedType "sqlalchemy.types.UserDefinedType") must include the
-[`ExternalType.cache_ok`](https://docs.sqlalchemy.org/en/20/core/type_api.html#sqlalchemy.types.ExternalType.cache_ok "sqlalchemy.types.ExternalType.cache_ok") attribute in their definition, including for
-all derived subclasses, following the guidelines described in the docstring
-for [`ExternalType.cache_ok`](https://docs.sqlalchemy.org/en/20/core/type_api.html#sqlalchemy.types.ExternalType.cache_ok "sqlalchemy.types.ExternalType.cache_ok"). As before, if these datatypes are
-imported from third party libraries, consult with the maintainers of that
-library so that they may provide the necessary changes to their library and
-publish a new release.
-
-- Third party or user defined SQL constructs that subclass from classes such
-as [`ClauseElement`](https://docs.sqlalchemy.org/en/20/core/foundation.html#sqlalchemy.sql.expression.ClauseElement "sqlalchemy.sql.expression.ClauseElement"), [`Column`](https://docs.sqlalchemy.org/en/20/core/metadata.html#sqlalchemy.schema.Column "sqlalchemy.schema.Column"), [`Insert`](https://docs.sqlalchemy.org/en/20/core/dml.html#sqlalchemy.sql.expression.Insert "sqlalchemy.sql.expression.Insert")
-etc, including simple subclasses as well as those which are designed to
-work with the [Custom SQL Constructs and Compilation Extension](https://docs.sqlalchemy.org/en/20/core/compiler.html), should normally
-include the [`HasCacheKey.inherit_cache`](https://docs.sqlalchemy.org/en/20/core/foundation.html#sqlalchemy.sql.traversals.HasCacheKey.inherit_cache "sqlalchemy.sql.traversals.HasCacheKey.inherit_cache") attribute set to `True`
-or `False` based on the design of the construct, following the guidelines
-described at [Enabling Caching Support for Custom Constructs](https://docs.sqlalchemy.org/en/20/core/compiler.html#compilerext-caching).
-
-
-See also
-
-[Estimating Cache Performance Using Logging](https://docs.sqlalchemy.org/en/20/core/connections.html#sql-caching-logging) \- background on observing cache behavior
-and efficiency
-
-[Why is my application slow after upgrading to 1.4 and/or 2.x?](https://docs.sqlalchemy.org/en/20/faq/performance.html#faq-new-caching) \- in the [Frequently Asked Questions](https://docs.sqlalchemy.org/en/20/faq/index.html) section
-
-### Compiler StrSQLCompiler can’t render element of type <element type> [¶](https://docs.sqlalchemy.org/en/20/errors.html\#compiler-strsqlcompiler-can-t-render-element-of-type-element-type "Link to this heading")
-
-This error usually occurs when attempting to stringify a SQL expression
-construct that includes elements which are not part of the default compilation;
-in this case, the error will be against the [`StrSQLCompiler`](https://docs.sqlalchemy.org/en/20/core/internals.html#sqlalchemy.sql.compiler.StrSQLCompiler "sqlalchemy.sql.compiler.StrSQLCompiler") class.
-In less common cases, it can also occur when the wrong kind of SQL expression
-is used with a particular type of database backend; in those cases, other
-kinds of SQL compiler classes will be named, such as `SQLCompiler` or
-`sqlalchemy.dialects.postgresql.PGCompiler`. The guidance below is
-more specific to the “stringification” use case but describes the general
-background as well.
-
-Normally, a Core SQL construct or ORM [`Query`](https://docs.sqlalchemy.org/en/20/orm/queryguide/query.html#sqlalchemy.orm.Query "sqlalchemy.orm.Query") object can be stringified
-directly, such as when we use `print()`:
-
-```
->>> from sqlalchemy import column
->>> print(column("x") == 5)
-
-x = :x_1
-
-```
-
-Copy to clipboard
-
-When the above SQL expression is stringified, the [`StrSQLCompiler`](https://docs.sqlalchemy.org/en/20/core/internals.html#sqlalchemy.sql.compiler.StrSQLCompiler "sqlalchemy.sql.compiler.StrSQLCompiler")
-compiler class is used, which is a special statement compiler that is invoked
-when a construct is stringified without any dialect-specific information.
-
-However, there are many constructs that are specific to some particular kind
-of database dialect, for which the [`StrSQLCompiler`](https://docs.sqlalchemy.org/en/20/core/internals.html#sqlalchemy.sql.compiler.StrSQLCompiler "sqlalchemy.sql.compiler.StrSQLCompiler") doesn’t know how
-to turn into a string, such as the PostgreSQL
-[INSERT…ON CONFLICT (Upsert)](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#postgresql-insert-on-conflict) construct:
-
-```
->>> from sqlalchemy.dialects.postgresql import insert
->>> from sqlalchemy import table, column
->>> my_table = table("my_table", column("x"), column("y"))
->>> insert_stmt = insert(my_table).values(x="foo")
->>> insert_stmt = insert_stmt.on_conflict_do_nothing(index_elements=["y"])
->>> print(insert_stmt)
-Traceback (most recent call last):
-
-...
-
-sqlalchemy.exc.UnsupportedCompilationError:
-Compiler <sqlalchemy.sql.compiler.StrSQLCompiler object at 0x7f04fc17e320>
-can't render element of type
-<class 'sqlalchemy.dialects.postgresql.dml.OnConflictDoNothing'>
-```
-
-Copy to clipboard
-
-In order to stringify constructs that are specific to particular backend,
-the [`ClauseElement.compile()`](https://docs.sqlalchemy.org/en/20/core/foundation.html#sqlalchemy.sql.expression.ClauseElement.compile "sqlalchemy.sql.expression.ClauseElement.compile") method must be used, passing either an
-[`Engine`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Engine "sqlalchemy.engine.Engine") or a [`Dialect`](https://docs.sqlalchemy.org/en/20/core/internals.html#sqlalchemy.engine.Dialect "sqlalchemy.engine.Dialect") object which will invoke the correct
-compiler. Below we use a PostgreSQL dialect:
-
-```
->>> from sqlalchemy.dialects import postgresql
->>> print(insert_stmt.compile(dialect=postgresql.dialect()))
-
-INSERT INTO my_table (x) VALUES (%(x)s) ON CONFLICT (y) DO NOTHING
-
-```
-
-Copy to clipboard
-
-For an ORM [`Query`](https://docs.sqlalchemy.org/en/20/orm/queryguide/query.html#sqlalchemy.orm.Query "sqlalchemy.orm.Query") object, the statement can be accessed using the
-`Query.statement` accessor:
-
-```
-statement = query.statement
-print(statement.compile(dialect=postgresql.dialect()))
-```
-
-Copy to clipboard
-
-See the FAQ link below for additional detail on direct stringification /
-compilation of SQL elements.
-
-See also
-
-[How do I render SQL expressions as strings, possibly with bound parameters inlined?](https://docs.sqlalchemy.org/en/20/faq/sqlexpressions.html#faq-sql-expression-string)
-
-### TypeError: <operator> not supported between instances of ‘ColumnProperty’ and <something> [¶](https://docs.sqlalchemy.org/en/20/errors.html\#typeerror-operator-not-supported-between-instances-of-columnproperty-and-something "Link to this heading")
-
-This often occurs when attempting to use a [`column_property()`](https://docs.sqlalchemy.org/en/20/orm/mapping_api.html#sqlalchemy.orm.column_property "sqlalchemy.orm.column_property") or
-[`deferred()`](https://docs.sqlalchemy.org/en/20/orm/queryguide/columns.html#sqlalchemy.orm.deferred "sqlalchemy.orm.deferred") object in the context of a SQL expression, usually within
-declarative such as:
-
-```
-class Bar(Base):
-    __tablename__ = "bar"
-
-    id = Column(Integer, primary_key=True)
-    cprop = deferred(Column(Integer))
-
-    __table_args__ = (CheckConstraint(cprop > 5),)
-```
-
-Copy to clipboard
-
-Above, the `cprop` attribute is used inline before it has been mapped,
-however this `cprop` attribute is not a [`Column`](https://docs.sqlalchemy.org/en/20/core/metadata.html#sqlalchemy.schema.Column "sqlalchemy.schema.Column"),
-it’s a [`ColumnProperty`](https://docs.sqlalchemy.org/en/20/orm/internals.html#sqlalchemy.orm.ColumnProperty "sqlalchemy.orm.ColumnProperty"), which is an interim object and therefore
-does not have the full functionality of either the [`Column`](https://docs.sqlalchemy.org/en/20/core/metadata.html#sqlalchemy.schema.Column "sqlalchemy.schema.Column") object
-or the [`InstrumentedAttribute`](https://docs.sqlalchemy.org/en/20/orm/internals.html#sqlalchemy.orm.InstrumentedAttribute "sqlalchemy.orm.InstrumentedAttribute") object that will be mapped onto the
-`Bar` class once the declarative process is complete.
-
-While the [`ColumnProperty`](https://docs.sqlalchemy.org/en/20/orm/internals.html#sqlalchemy.orm.ColumnProperty "sqlalchemy.orm.ColumnProperty") does have a `__clause_element__()` method,
-which allows it to work in some column-oriented contexts, it can’t work in an
-open-ended comparison context as illustrated above, since it has no Python
-`__eq__()` method that would allow it to interpret the comparison to the
-number “5” as a SQL expression and not a regular Python comparison.
-
-The solution is to access the [`Column`](https://docs.sqlalchemy.org/en/20/core/metadata.html#sqlalchemy.schema.Column "sqlalchemy.schema.Column") directly using the
-[`ColumnProperty.expression`](https://docs.sqlalchemy.org/en/20/orm/internals.html#sqlalchemy.orm.ColumnProperty.expression "sqlalchemy.orm.ColumnProperty.expression") attribute:
-
-```
-class Bar(Base):
-    __tablename__ = "bar"
-
-    id = Column(Integer, primary_key=True)
-    cprop = deferred(Column(Integer))
-
-    __table_args__ = (CheckConstraint(cprop.expression > 5),)
-```
-
-Copy to clipboard
-
-### A value is required for bind parameter <x> (in parameter group <y>) [¶](https://docs.sqlalchemy.org/en/20/errors.html\#a-value-is-required-for-bind-parameter-x-in-parameter-group-y "Link to this heading")
-
-This error occurs when a statement makes use of [`bindparam()`](https://docs.sqlalchemy.org/en/20/core/sqlelement.html#sqlalchemy.sql.expression.bindparam "sqlalchemy.sql.expression.bindparam") either
-implicitly or explicitly and does not provide a value when the statement
-is executed:
-
-```
-stmt = select(table.c.column).where(table.c.id == bindparam("my_param"))
-
-result = conn.execute(stmt)
-```
-
-Copy to clipboard
-
-Above, no value has been provided for the parameter “my\_param”. The correct
-approach is to provide a value:
-
-```
-result = conn.execute(stmt, {"my_param": 12})
-```
-
-Copy to clipboard
-
-When the message takes the form “a value is required for bind parameter <x>
-in parameter group <y>”, the message is referring to the “executemany” style
-of execution. In this case, the statement is typically an INSERT, UPDATE,
-or DELETE and a list of parameters is being passed. In this format, the
-statement may be generated dynamically to include parameter positions for
-every parameter given in the argument list, where it will use the
-**first set of parameters** to determine what these should be.
-
-For example, the statement below is calculated based on the first parameter
-set to require the parameters, “a”, “b”, and “c” - these names determine
-the final string format of the statement which will be used for each
-set of parameters in the list. As the second entry does not contain “b”,
-this error is generated:
-
-```
-m = MetaData()
-t = Table("t", m, Column("a", Integer), Column("b", Integer), Column("c", Integer))
-
-e.execute(
-    t.insert(),
-    [\
-        {"a": 1, "b": 2, "c": 3},\
-        {"a": 2, "c": 4},\
-        {"a": 3, "b": 4, "c": 5},\
-    ],
-)
-```
-
-Copy to clipboard
-
-```
-sqlalchemy.exc.StatementError: (sqlalchemy.exc.InvalidRequestError)
-A value is required for bind parameter 'b', in parameter group 1
-[SQL: u'INSERT INTO t (a, b, c) VALUES (?, ?, ?)']
-[parameters: [{'a': 1, 'c': 3, 'b': 2}, {'a': 2, 'c': 4}, {'a': 3, 'c': 5, 'b': 4}]]
-```
-
-Copy to clipboard
-
-Since “b” is required, pass it as `None` so that the INSERT may proceed:
-
-```
-e.execute(
-    t.insert(),
-    [\
-        {"a": 1, "b": 2, "c": 3},\
-        {"a": 2, "b": None, "c": 4},\
-        {"a": 3, "b": 4, "c": 5},\
-    ],
-)
-```
-
-Copy to clipboard
-
-See also
-
-[Sending Parameters](https://docs.sqlalchemy.org/en/20/tutorial/dbapi_transactions.html#tutorial-sending-parameters)
-
-### Expected FROM clause, got Select. To create a FROM clause, use the .subquery() method [¶](https://docs.sqlalchemy.org/en/20/errors.html\#expected-from-clause-got-select-to-create-a-from-clause-use-the-subquery-method "Link to this heading")
-
-This refers to a change made as of SQLAlchemy 1.4 where a SELECT statement as generated
-by a function such as [`select()`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.select "sqlalchemy.sql.expression.select"), but also including things like unions and textual
-SELECT expressions are no longer considered to be [`FromClause`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.FromClause "sqlalchemy.sql.expression.FromClause") objects and
-can’t be placed directly in the FROM clause of another SELECT statement without them
-being wrapped in a [`Subquery`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Subquery "sqlalchemy.sql.expression.Subquery") first. This is a major conceptual change in the
-Core and the full rationale is discussed at [A SELECT statement is no longer implicitly considered to be a FROM clause](https://docs.sqlalchemy.org/en/20/changelog/migration_14.html#change-4617).
-
-Given an example as:
-
-```
-m = MetaData()
-t = Table("t", m, Column("a", Integer), Column("b", Integer), Column("c", Integer))
-stmt = select(t)
-```
-
-Copy to clipboard
-
-Above, `stmt` represents a SELECT statement. The error is produced when we want
-to use `stmt` directly as a FROM clause in another SELECT, such as if we
-attempted to select from it:
-
-```
-new_stmt_1 = select(stmt)
-```
-
-Copy to clipboard
-
-Or if we wanted to use it in a FROM clause such as in a JOIN:
-
-```
-new_stmt_2 = select(some_table).select_from(some_table.join(stmt))
-```
-
-Copy to clipboard
-
-In previous versions of SQLAlchemy, using a SELECT inside of another SELECT
-would produce a parenthesized, unnamed subquery. In most cases, this form of
-SQL is not very useful as databases like MySQL and PostgreSQL require that
-subqueries in FROM clauses have named aliases, which means using the
-[`SelectBase.alias()`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.SelectBase.alias "sqlalchemy.sql.expression.SelectBase.alias") method or as of 1.4 using the
-[`SelectBase.subquery()`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.SelectBase.subquery "sqlalchemy.sql.expression.SelectBase.subquery") method to produce this. On other databases, it
-is still much clearer for the subquery to have a name to resolve any ambiguity
-on future references to column names inside the subquery.
-
-Beyond the above practical reasons, there are a lot of other SQLAlchemy-oriented
-reasons the change is being made. The correct form of the above two statements
-therefore requires that [`SelectBase.subquery()`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.SelectBase.subquery "sqlalchemy.sql.expression.SelectBase.subquery") is used:
-
-```
-subq = stmt.subquery()
-
-new_stmt_1 = select(subq)
-
-new_stmt_2 = select(some_table).select_from(some_table.join(subq))
-```
-
-Copy to clipboard
-
-See also
-
-[A SELECT statement is no longer implicitly considered to be a FROM clause](https://docs.sqlalchemy.org/en/20/changelog/migration_14.html#change-4617)
-
-### An alias is being generated automatically for raw clauseelement [¶](https://docs.sqlalchemy.org/en/20/errors.html\#an-alias-is-being-generated-automatically-for-raw-clauseelement "Link to this heading")
-
-Added in version 1.4.26.
-
-This deprecation warning refers to a very old and likely not well known pattern
-that applies to the legacy [`Query.join()`](https://docs.sqlalchemy.org/en/20/orm/queryguide/query.html#sqlalchemy.orm.Query.join "sqlalchemy.orm.Query.join") method as well as the
-[2.0 style](https://docs.sqlalchemy.org/en/20/glossary.html#term-2.0-style) [`Select.join()`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Select.join "sqlalchemy.sql.expression.Select.join") method, where a join can be stated
-in terms of a [`relationship()`](https://docs.sqlalchemy.org/en/20/orm/relationship_api.html#sqlalchemy.orm.relationship "sqlalchemy.orm.relationship") but the target is the
-[`Table`](https://docs.sqlalchemy.org/en/20/core/metadata.html#sqlalchemy.schema.Table "sqlalchemy.schema.Table") or other Core selectable to which the class is mapped,
-rather than an ORM entity such as a mapped class or [`aliased()`](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#sqlalchemy.orm.aliased "sqlalchemy.orm.aliased")
-construct:
-
-```
-a1 = Address.__table__
-
-q = (
-    s.query(User)
-    .join(a1, User.addresses)
-    .filter(Address.email_address == "ed@foo.com")
-    .all()
-)
-```
-
-Copy to clipboard
-
-The above pattern also allows an arbitrary selectable, such as
-a Core [`Join`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Join "sqlalchemy.sql.expression.Join") or [`Alias`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Alias "sqlalchemy.sql.expression.Alias") object,
-however there is no automatic adaptation of this element, meaning the
-Core element would need to be referenced directly:
-
-```
-a1 = Address.__table__.alias()
-
-q = (
-    s.query(User)
-    .join(a1, User.addresses)
-    .filter(a1.c.email_address == "ed@foo.com")
-    .all()
-)
-```
-
-Copy to clipboard
-
-The correct way to specify a join target is always by using the mapped
-class itself or an [`aliased`](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#sqlalchemy.orm.aliased "sqlalchemy.orm.aliased") object, in the latter case using the
-[`PropComparator.of_type()`](https://docs.sqlalchemy.org/en/20/orm/internals.html#sqlalchemy.orm.PropComparator.of_type "sqlalchemy.orm.PropComparator.of_type") modifier to set up an alias:
-
-```
-# normal join to relationship entity
-q = s.query(User).join(User.addresses).filter(Address.email_address == "ed@foo.com")
-
-# name Address target explicitly, not necessary but legal
-q = (
-    s.query(User)
-    .join(Address, User.addresses)
-    .filter(Address.email_address == "ed@foo.com")
-)
-```
-
-Copy to clipboard
-
-Join to an alias:
-
-```
-from sqlalchemy.orm import aliased
-
-a1 = aliased(Address)
-
-# of_type() form; recommended
-q = (
-    s.query(User)
-    .join(User.addresses.of_type(a1))
-    .filter(a1.email_address == "ed@foo.com")
-)
-
-# target, onclause form
-q = s.query(User).join(a1, User.addresses).filter(a1.email_address == "ed@foo.com")
-```
-
-Copy to clipboard
-
-### An alias is being generated automatically due to overlapping tables [¶](https://docs.sqlalchemy.org/en/20/errors.html\#an-alias-is-being-generated-automatically-due-to-overlapping-tables "Link to this heading")
-
-Added in version 1.4.26.
-
-This warning is typically generated when querying using the
-[`Select.join()`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Select.join "sqlalchemy.sql.expression.Select.join") method or the legacy [`Query.join()`](https://docs.sqlalchemy.org/en/20/orm/queryguide/query.html#sqlalchemy.orm.Query.join "sqlalchemy.orm.Query.join") method
-with mappings that involve joined table inheritance. The issue is that when
-joining between two joined inheritance models that share a common base table, a
-proper SQL JOIN between the two entities cannot be formed without applying an
-alias to one side or the other; SQLAlchemy applies an alias to the right side
-of the join. For example given a joined inheritance mapping as:
-
-```
-class Employee(Base):
-    __tablename__ = "employee"
-    id = Column(Integer, primary_key=True)
-    manager_id = Column(ForeignKey("manager.id"))
-    name = Column(String(50))
-    type = Column(String(50))
-
-    reports_to = relationship("Manager", foreign_keys=manager_id)
-
-    __mapper_args__ = {
-        "polymorphic_identity": "employee",
-        "polymorphic_on": type,
-    }
-
-class Manager(Employee):
-    __tablename__ = "manager"
-    id = Column(Integer, ForeignKey("employee.id"), primary_key=True)
-
-    __mapper_args__ = {
-        "polymorphic_identity": "manager",
-        "inherit_condition": id == Employee.id,
-    }
-```
-
-Copy to clipboard
-
-The above mapping includes a relationship between the `Employee` and
-`Manager` classes. Since both classes make use of the “employee” database
-table, from a SQL perspective this is a
-[self referential relationship](https://docs.sqlalchemy.org/en/20/orm/self_referential.html#self-referential). If we wanted to
-query from both the `Employee` and `Manager` models using a join, at the
-SQL level the “employee” table needs to be included twice in the query, which
-means it must be aliased. When we create such a join using the SQLAlchemy
-ORM, we get SQL that looks like the following:
-
-```
->>> stmt = select(Employee, Manager).join(Employee.reports_to)
->>> print(stmt)
-
-SELECT employee.id, employee.manager_id, employee.name,
-employee.type, manager_1.id AS id_1, employee_1.id AS id_2,
-employee_1.manager_id AS manager_id_1, employee_1.name AS name_1,
-employee_1.type AS type_1
-FROM employee JOIN
-(employee AS employee_1 JOIN manager AS manager_1 ON manager_1.id = employee_1.id)
-ON manager_1.id = employee.manager_id
-
-```
-
-Copy to clipboard
-
-Above, the SQL selects FROM the `employee` table, representing the
-`Employee` entity in the query. It then joins to a right-nested join of
-`employee AS employee_1 JOIN manager AS manager_1`, where the `employee`
-table is stated again, except as an anonymous alias `employee_1`. This is the
-‘automatic generation of an alias’ to which the warning message refers.
-
-When SQLAlchemy loads ORM rows that each contain an `Employee` and a
-`Manager` object, the ORM must adapt rows from what above is the
-`employee_1` and `manager_1` table aliases into those of the un-aliased
-`Manager` class. This process is internally complex and does not accommodate
-for all API features, notably when trying to use eager loading features such as
-[`contains_eager()`](https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html#sqlalchemy.orm.contains_eager "sqlalchemy.orm.contains_eager") with more deeply nested queries than are shown
-here. As the pattern is unreliable for more complex scenarios and involves
-implicit decisionmaking that is difficult to anticipate and follow,
-the warning is emitted and this pattern may be considered a legacy feature. The
-better way to write this query is to use the same patterns that apply to any
-other self-referential relationship, which is to use the [`aliased()`](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#sqlalchemy.orm.aliased "sqlalchemy.orm.aliased")
-construct explicitly. For joined-inheritance and other join-oriented mappings,
-it is usually desirable to add the use of the [`aliased.flat`](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#sqlalchemy.orm.aliased.params.flat "sqlalchemy.orm.aliased")
-parameter, which will allow a JOIN of two or more tables to be aliased by
-applying an alias to the individual tables within the join, rather than
-embedding the join into a new subquery:
-
-```
->>> from sqlalchemy.orm import aliased
->>> manager_alias = aliased(Manager, flat=True)
->>> stmt = select(Employee, manager_alias).join(Employee.reports_to.of_type(manager_alias))
->>> print(stmt)
-
-SELECT employee.id, employee.manager_id, employee.name,
-employee.type, manager_1.id AS id_1, employee_1.id AS id_2,
-employee_1.manager_id AS manager_id_1, employee_1.name AS name_1,
-employee_1.type AS type_1
-FROM employee JOIN
-(employee AS employee_1 JOIN manager AS manager_1 ON manager_1.id = employee_1.id)
-ON manager_1.id = employee.manager_id
-
-```
-
-Copy to clipboard
-
-If we then wanted to use [`contains_eager()`](https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html#sqlalchemy.orm.contains_eager "sqlalchemy.orm.contains_eager") to populate the
-`reports_to` attribute, we refer to the alias:
-
-```
->>> stmt = (
-...     select(Employee)
-...     .join(Employee.reports_to.of_type(manager_alias))
-...     .options(contains_eager(Employee.reports_to.of_type(manager_alias)))
-... )
-```
-
-Copy to clipboard
-
-Without using the explicit [`aliased()`](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#sqlalchemy.orm.aliased "sqlalchemy.orm.aliased") object, in some more nested
-cases the [`contains_eager()`](https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html#sqlalchemy.orm.contains_eager "sqlalchemy.orm.contains_eager") option does not have enough context to
-know where to get its data from, in the case that the ORM is “auto-aliasing”
-in a very nested context. Therefore it’s best not to rely on this feature
-and instead keep the SQL construction as explicit as possible.
-
-## Object Relational Mapping [¶](https://docs.sqlalchemy.org/en/20/errors.html\#object-relational-mapping "Link to this heading")
-
-### IllegalStateChangeError and concurrency exceptions [¶](https://docs.sqlalchemy.org/en/20/errors.html\#illegalstatechangeerror-and-concurrency-exceptions "Link to this heading")
-
-SQLAlchemy 2.0 introduced a new system described at [Session raises proactively when illegal concurrent or reentrant access is detected](https://docs.sqlalchemy.org/en/20/changelog/whatsnew_20.html#change-7433), which
-proactively detects concurrent methods being invoked on an individual instance of
-the [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session")
-object and by extension the [`AsyncSession`](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncSession "sqlalchemy.ext.asyncio.AsyncSession") proxy object.
-These concurrent access calls typically, though not exclusively, would occur
-when a single instance of [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session") is shared among multiple
-concurrent threads without such access being synchronized, or similarly
-when a single instance of [`AsyncSession`](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncSession "sqlalchemy.ext.asyncio.AsyncSession") is shared among
-multiple concurrent tasks (such as when using a function like `asyncio.gather()`).
-These use patterns are not the appropriate use of these objects, where without
-the proactive warning system SQLAlchemy implements would still otherwise produce
-invalid state within the objects, producing hard-to-debug errors including
-driver-level errors on the database connections themselves.
-
-Instances of [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session") and [`AsyncSession`](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncSession "sqlalchemy.ext.asyncio.AsyncSession") are
-**mutable, stateful objects with no built-in synchronization** of method calls,
-and represent a **single, ongoing database transaction** upon a single database
-connection at a time for a particular [`Engine`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Engine "sqlalchemy.engine.Engine") or [`AsyncEngine`](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncEngine "sqlalchemy.ext.asyncio.AsyncEngine")
-to which the object is bound (note that these objects both support being bound
-to multiple engines at once, however in this case there will still be only one
-connection per engine in play within the scope of a transaction). A single
-database transaction is not an appropriate target for concurrent SQL commands;
-instead, an application that runs concurrent database operations should use
-concurrent transactions. For these objects then it follows that the appropriate
-pattern is [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session") per thread, or [`AsyncSession`](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncSession "sqlalchemy.ext.asyncio.AsyncSession")
-per task.
-
-For more background on concurrency see the section
-[Is the Session thread-safe? Is AsyncSession safe to share in concurrent tasks?](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#session-faq-threadsafe).
-
-### Parent instance <x> is not bound to a Session; (lazy load/deferred load/refresh/etc.) operation cannot proceed [¶](https://docs.sqlalchemy.org/en/20/errors.html\#parent-instance-x-is-not-bound-to-a-session-lazy-load-deferred-load-refresh-etc-operation-cannot-proceed "Link to this heading")
-
-This is likely the most common error message when dealing with the ORM, and it
-occurs as a result of the nature of a technique the ORM makes wide use of known
-as [lazy loading](https://docs.sqlalchemy.org/en/20/glossary.html#term-lazy-loading). Lazy loading is a common object-relational pattern
-whereby an object that’s persisted by the ORM maintains a proxy to the database
-itself, such that when various attributes upon the object are accessed, their
-value may be retrieved from the database _lazily_. The advantage to this
-approach is that objects can be retrieved from the database without having
-to load all of their attributes or related data at once, and instead only that
-data which is requested can be delivered at that time. The major disadvantage
-is basically a mirror image of the advantage, which is that if lots of objects
-are being loaded which are known to require a certain set of data in all cases,
-it is wasteful to load that additional data piecemeal.
-
-Another caveat of lazy loading beyond the usual efficiency concerns is that
-in order for lazy loading to proceed, the object has to **remain associated**
-**with a Session** in order to be able to retrieve its state. This error message
-means that an object has become de-associated with its [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session") and
-is being asked to lazy load data from the database.
-
-The most common reason that objects become detached from their [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session")
-is that the session itself was closed, typically via the [`Session.close()`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session.close "sqlalchemy.orm.Session.close")
-method. The objects will then live on to be accessed further, very often
-within web applications where they are delivered to a server-side templating
-engine and are asked for further attributes which they cannot load.
-
-Mitigation of this error is via these techniques:
-
-- **Try not to have detached objects; don’t close the session prematurely** \- Often, applications will close
-out a transaction before passing off related objects to some other system
-which then fails due to this error. Sometimes the transaction doesn’t need
-to be closed so soon; an example is the web application closes out
-the transaction before the view is rendered. This is often done in the name
-of “correctness”, but may be seen as a mis-application of “encapsulation”,
-as this term refers to code organization, not actual actions. The template that
-uses an ORM object is making use of the [proxy pattern](https://en.wikipedia.org/wiki/Proxy_pattern)
-which keeps database logic encapsulated from the caller. If the
-[`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session") can be held open until the lifespan of the objects are done,
-this is the best approach.
-
-- **Otherwise, load everything that’s needed up front** \- It is very often impossible to
-keep the transaction open, especially in more complex applications that need
-to pass objects off to other systems that can’t run in the same context
-even though they’re in the same process. In this case, the application
-should prepare to deal with [detached](https://docs.sqlalchemy.org/en/20/glossary.html#term-detached) objects,
-and should try to make appropriate use of [eager loading](https://docs.sqlalchemy.org/en/20/glossary.html#term-eager-loading) to ensure
-that objects have what they need up front.
-
-- **And importantly, set expire\_on\_commit to False** \- When using detached objects, the
-most common reason objects need to re-load data is because they were expired
-from the last call to [`Session.commit()`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session.commit "sqlalchemy.orm.Session.commit"). This expiration should
-not be used when dealing with detached objects; so the
-[`Session.expire_on_commit`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session.params.expire_on_commit "sqlalchemy.orm.Session") parameter be set to `False`.
-By preventing the objects from becoming expired outside of the transaction,
-the data which was loaded will remain present and will not incur additional
-lazy loads when that data is accessed.
-
-Note also that [`Session.rollback()`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session.rollback "sqlalchemy.orm.Session.rollback") method unconditionally expires
-all contents in the [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session") and should also be avoided in
-non-error scenarios.
-
-
-
-See also
-
-
-
-[Relationship Loading Techniques](https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html) \- detailed documentation on eager loading and other
-relationship-oriented loading techniques
-
-
-
-[Committing](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#session-committing) \- background on session commit
-
-
-
-[Refreshing / Expiring](https://docs.sqlalchemy.org/en/20/orm/session_state_management.html#session-expire) \- background on attribute expiry
-
-
-### This Session’s transaction has been rolled back due to a previous exception during flush [¶](https://docs.sqlalchemy.org/en/20/errors.html\#this-session-s-transaction-has-been-rolled-back-due-to-a-previous-exception-during-flush "Link to this heading")
-
-The flush process of the [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session"), described at
-[Flushing](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#session-flushing), will roll back the database transaction if an error is
-encountered, in order to maintain internal consistency. However, once this
-occurs, the session’s transaction is now “inactive” and must be explicitly
-rolled back by the calling application, in the same way that it would otherwise
-need to be explicitly committed if a failure had not occurred.
-
-This is a common error when using the ORM and typically applies to an
-application that doesn’t yet have correct “framing” around its
-[`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session") operations. Further detail is described in the FAQ at
-[“This Session’s transaction has been rolled back due to a previous exception during flush.” (or similar)](https://docs.sqlalchemy.org/en/20/faq/sessions.html#faq-session-rollback).
-
-### For relationship <relationship>, delete-orphan cascade is normally configured only on the “one” side of a one-to-many relationship, and not on the “many” side of a many-to-one or many-to-many relationship. [¶](https://docs.sqlalchemy.org/en/20/errors.html\#for-relationship-relationship-delete-orphan-cascade-is-normally-configured-only-on-the-one-side-of-a-one-to-many-relationship-and-not-on-the-many-side-of-a-many-to-one-or-many-to-many-relationship "Link to this heading")
-
-This error arises when the “delete-orphan” [cascade](https://docs.sqlalchemy.org/en/20/orm/cascades.html#unitofwork-cascades)
-is set on a many-to-one or many-to-many relationship, such as:
-
-```
-class A(Base):
-    __tablename__ = "a"
-
-    id = Column(Integer, primary_key=True)
-
-    bs = relationship("B", back_populates="a")
-
-class B(Base):
-    __tablename__ = "b"
-    id = Column(Integer, primary_key=True)
-    a_id = Column(ForeignKey("a.id"))
-
-    # this will emit the error message when the mapper
-    # configuration step occurs
-    a = relationship("A", back_populates="bs", cascade="all, delete-orphan")
-
-configure_mappers()
-```
-
-Copy to clipboard
-
-Above, the “delete-orphan” setting on `B.a` indicates the intent that
-when every `B` object that refers to a particular `A` is deleted, that the
-`A` should then be deleted as well. That is, it expresses that the “orphan”
-which is being deleted would be an `A` object, and it becomes an “orphan”
-when every `B` that refers to it is deleted.
-
-The “delete-orphan” cascade model does not support this functionality. The
-“orphan” consideration is only made in terms of the deletion of a single object
-which would then refer to zero or more objects that are now “orphaned” by
-this single deletion, which would result in those objects being deleted as
-well. In other words, it is designed only to track the creation of “orphans”
-based on the removal of one and only one “parent” object per orphan, which is
-the natural case in a one-to-many relationship where a deletion of the
-object on the “one” side results in the subsequent deletion of the related
-items on the “many” side.
-
-The above mapping in support of this functionality would instead place the
-cascade setting on the one-to-many side, which looks like:
-
-```
-class A(Base):
-    __tablename__ = "a"
-
-    id = Column(Integer, primary_key=True)
-
-    bs = relationship("B", back_populates="a", cascade="all, delete-orphan")
-
-class B(Base):
-    __tablename__ = "b"
-    id = Column(Integer, primary_key=True)
-    a_id = Column(ForeignKey("a.id"))
-
-    a = relationship("A", back_populates="bs")
-```
-
-Copy to clipboard
-
-Where the intent is expressed that when an `A` is deleted, all of the
-`B` objects to which it refers are also deleted.
-
-The error message then goes on to suggest the usage of the
-[`relationship.single_parent`](https://docs.sqlalchemy.org/en/20/orm/relationship_api.html#sqlalchemy.orm.relationship.params.single_parent "sqlalchemy.orm.relationship") flag. This flag may be used
-to enforce that a relationship which is capable of having many objects
-refer to a particular object will in fact have only **one** object referring
-to it at a time. It is used for legacy or other less ideal
-database schemas where the foreign key relationships suggest a “many”
-collection, however in practice only one object would actually refer
-to a given target object at at time. This uncommon scenario
-can be demonstrated in terms of the above example as follows:
-
-```
-class A(Base):
-    __tablename__ = "a"
-
-    id = Column(Integer, primary_key=True)
-
-    bs = relationship("B", back_populates="a")
-
-class B(Base):
-    __tablename__ = "b"
-    id = Column(Integer, primary_key=True)
-    a_id = Column(ForeignKey("a.id"))
-
-    a = relationship(
-        "A",
-        back_populates="bs",
-        single_parent=True,
-        cascade="all, delete-orphan",
-    )
-```
-
-Copy to clipboard
-
-The above configuration will then install a validator which will enforce
-that only one `B` may be associated with an `A` at at time, within
-the scope of the `B.a` relationship:
-
-```
->>> b1 = B()
->>> b2 = B()
->>> a1 = A()
->>> b1.a = a1
->>> b2.a = a1
-sqlalchemy.exc.InvalidRequestError: Instance <A at 0x7eff44359350> is
-already associated with an instance of <class '__main__.B'> via its
-B.a attribute, and is only allowed a single parent.
-```
-
-Copy to clipboard
-
-Note that this validator is of limited scope and will not prevent multiple
-“parents” from being created via the other direction. For example, it will
-not detect the same setting in terms of `A.bs`:
-
-```
->>> a1.bs = [b1, b2]
->>> session.add_all([a1, b1, b2])
->>> session.commit()
-
-INSERT INTO a DEFAULT VALUES
-()
-INSERT INTO b (a_id) VALUES (?)
-(1,)
-INSERT INTO b (a_id) VALUES (?)
-(1,)
-
-```
-
-Copy to clipboard
-
-However, things will not go as expected later on, as the “delete-orphan” cascade
-will continue to work in terms of a **single** lead object, meaning if we
-delete **either** of the `B` objects, the `A` is deleted. The other `B` stays
-around, where the ORM will usually be smart enough to set the foreign key attribute
-to NULL, but this is usually not what’s desired:
-
-```
->>> session.delete(b1)
->>> session.commit()
-
-UPDATE b SET a_id=? WHERE b.id = ?
-(None, 2)
-DELETE FROM b WHERE b.id = ?
-(1,)
-DELETE FROM a WHERE a.id = ?
-(1,)
-COMMIT
-
-```
-
-Copy to clipboard
-
-For all the above examples, similar logic applies to the calculus of a
-many-to-many relationship; if a many-to-many relationship sets single\_parent=True
-on one side, that side can use the “delete-orphan” cascade, however this is
-very unlikely to be what someone actually wants as the point of a many-to-many
-relationship is so that there can be many objects referring to an object
-in either direction.
-
-Overall, “delete-orphan” cascade is usually applied
-on the “one” side of a one-to-many relationship so that it deletes objects
-in the “many” side, and not the other way around.
-
-Changed in version 1.3.18: The text of the “delete-orphan” error message
-when used on a many-to-one or many-to-many relationship has been updated
-to be more descriptive.
-
-See also
-
-[Cascades](https://docs.sqlalchemy.org/en/20/orm/cascades.html#unitofwork-cascades)
-
-[delete-orphan](https://docs.sqlalchemy.org/en/20/orm/cascades.html#cascade-delete-orphan)
-
-[Instance <instance> is already associated with an instance of <instance> via its <attribute> attribute, and is only allowed a single parent.](https://docs.sqlalchemy.org/en/20/errors.html#error-bbf1)
-
-### Instance <instance> is already associated with an instance of <instance> via its <attribute> attribute, and is only allowed a single parent. [¶](https://docs.sqlalchemy.org/en/20/errors.html\#instance-instance-is-already-associated-with-an-instance-of-instance-via-its-attribute-attribute-and-is-only-allowed-a-single-parent "Link to this heading")
-
-This error is emitted when the [`relationship.single_parent`](https://docs.sqlalchemy.org/en/20/orm/relationship_api.html#sqlalchemy.orm.relationship.params.single_parent "sqlalchemy.orm.relationship") flag
-is used, and more than one object is assigned as the “parent” of an object at
-once.
-
-Given the following mapping:
-
-```
-class A(Base):
-    __tablename__ = "a"
-
-    id = Column(Integer, primary_key=True)
-
-class B(Base):
-    __tablename__ = "b"
-    id = Column(Integer, primary_key=True)
-    a_id = Column(ForeignKey("a.id"))
-
-    a = relationship(
-        "A",
-        single_parent=True,
-        cascade="all, delete-orphan",
-    )
-```
-
-Copy to clipboard
-
-The intent indicates that no more than a single `B` object may refer
-to a particular `A` object at once:
-
-```
->>> b1 = B()
->>> b2 = B()
->>> a1 = A()
->>> b1.a = a1
->>> b2.a = a1
-sqlalchemy.exc.InvalidRequestError: Instance <A at 0x7eff44359350> is
-already associated with an instance of <class '__main__.B'> via its
-B.a attribute, and is only allowed a single parent.
-```
-
-Copy to clipboard
-
-When this error occurs unexpectedly, it is usually because the
-[`relationship.single_parent`](https://docs.sqlalchemy.org/en/20/orm/relationship_api.html#sqlalchemy.orm.relationship.params.single_parent "sqlalchemy.orm.relationship") flag was applied in response
-to the error message described at [For relationship <relationship>, delete-orphan cascade is normally configured only on the “one” side of a one-to-many relationship, and not on the “many” side of a many-to-one or many-to-many relationship.](https://docs.sqlalchemy.org/en/20/errors.html#error-bbf0), and the issue is in
-fact a misunderstanding of the “delete-orphan” cascade setting. See that
-message for details.
-
-See also
-
-[For relationship <relationship>, delete-orphan cascade is normally configured only on the “one” side of a one-to-many relationship, and not on the “many” side of a many-to-one or many-to-many relationship.](https://docs.sqlalchemy.org/en/20/errors.html#error-bbf0)
-
-### relationship X will copy column Q to column P, which conflicts with relationship(s): ‘Y’ [¶](https://docs.sqlalchemy.org/en/20/errors.html\#relationship-x-will-copy-column-q-to-column-p-which-conflicts-with-relationship-s-y "Link to this heading")
-
-This warning refers to the case when two or more relationships will write data
-to the same columns on flush, but the ORM does not have any means of
-coordinating these relationships together. Depending on specifics, the solution
-may be that two relationships need to be referenced by one another using
-[`relationship.back_populates`](https://docs.sqlalchemy.org/en/20/orm/relationship_api.html#sqlalchemy.orm.relationship.params.back_populates "sqlalchemy.orm.relationship"), or that one or more of the
-relationships should be configured with [`relationship.viewonly`](https://docs.sqlalchemy.org/en/20/orm/relationship_api.html#sqlalchemy.orm.relationship.params.viewonly "sqlalchemy.orm.relationship")
-to prevent conflicting writes, or sometimes that the configuration is fully
-intentional and should configure [`relationship.overlaps`](https://docs.sqlalchemy.org/en/20/orm/relationship_api.html#sqlalchemy.orm.relationship.params.overlaps "sqlalchemy.orm.relationship") to
-silence each warning.
-
-For the typical example that’s missing
-[`relationship.back_populates`](https://docs.sqlalchemy.org/en/20/orm/relationship_api.html#sqlalchemy.orm.relationship.params.back_populates "sqlalchemy.orm.relationship"), given the following mapping:
-
-```
-class Parent(Base):
-    __tablename__ = "parent"
-    id = Column(Integer, primary_key=True)
-    children = relationship("Child")
-
-class Child(Base):
-    __tablename__ = "child"
-    id = Column(Integer, primary_key=True)
-    parent_id = Column(ForeignKey("parent.id"))
-    parent = relationship("Parent")
-```
-
-Copy to clipboard
-
-The above mapping will generate warnings:
-
-```
-SAWarning: relationship 'Child.parent' will copy column parent.id to column child.parent_id,
-which conflicts with relationship(s): 'Parent.children' (copies parent.id to child.parent_id).
-```
-
-Copy to clipboard
-
-The relationships `Child.parent` and `Parent.children` appear to be in conflict.
-The solution is to apply [`relationship.back_populates`](https://docs.sqlalchemy.org/en/20/orm/relationship_api.html#sqlalchemy.orm.relationship.params.back_populates "sqlalchemy.orm.relationship"):
-
-```
-class Parent(Base):
-    __tablename__ = "parent"
-    id = Column(Integer, primary_key=True)
-    children = relationship("Child", back_populates="parent")
-
-class Child(Base):
-    __tablename__ = "child"
-    id = Column(Integer, primary_key=True)
-    parent_id = Column(ForeignKey("parent.id"))
-    parent = relationship("Parent", back_populates="children")
-```
-
-Copy to clipboard
-
-For more customized relationships where an “overlap” situation may be
-intentional and cannot be resolved, the [`relationship.overlaps`](https://docs.sqlalchemy.org/en/20/orm/relationship_api.html#sqlalchemy.orm.relationship.params.overlaps "sqlalchemy.orm.relationship")
-parameter may specify the names of relationships for which the warning should
-not take effect. This typically occurs for two or more relationships to the
-same underlying table that include custom
-[`relationship.primaryjoin`](https://docs.sqlalchemy.org/en/20/orm/relationship_api.html#sqlalchemy.orm.relationship.params.primaryjoin "sqlalchemy.orm.relationship") conditions that limit the related
-items in each case:
-
-```
-class Parent(Base):
-    __tablename__ = "parent"
-    id = Column(Integer, primary_key=True)
-    c1 = relationship(
-        "Child",
-        primaryjoin="and_(Parent.id == Child.parent_id, Child.flag == 0)",
-        backref="parent",
-        overlaps="c2, parent",
-    )
-    c2 = relationship(
-        "Child",
-        primaryjoin="and_(Parent.id == Child.parent_id, Child.flag == 1)",
-        overlaps="c1, parent",
-    )
-
-class Child(Base):
-    __tablename__ = "child"
-    id = Column(Integer, primary_key=True)
-    parent_id = Column(ForeignKey("parent.id"))
-
-    flag = Column(Integer)
-```
-
-Copy to clipboard
-
-Above, the ORM will know that the overlap between `Parent.c1`,
-`Parent.c2` and `Child.parent` is intentional.
-
-### Object cannot be converted to ‘persistent’ state, as this identity map is no longer valid. [¶](https://docs.sqlalchemy.org/en/20/errors.html\#object-cannot-be-converted-to-persistent-state-as-this-identity-map-is-no-longer-valid "Link to this heading")
-
-Added in version 1.4.26.
-
-This message was added to accommodate for the case where a
-[`Result`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Result "sqlalchemy.engine.Result") object that would yield ORM objects is iterated after
-the originating [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session") has been closed, or otherwise had its
-[`Session.expunge_all()`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session.expunge_all "sqlalchemy.orm.Session.expunge_all") method called. When a [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session")
-expunges all objects at once, the internal [identity map](https://docs.sqlalchemy.org/en/20/glossary.html#term-identity-map) used by that
-[`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session") is replaced with a new one, and the original one
-discarded. An unconsumed and unbuffered [`Result`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Result "sqlalchemy.engine.Result") object will
-internally maintain a reference to that now-discarded identity map. Therefore,
-when the [`Result`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Result "sqlalchemy.engine.Result") is consumed, the objects that would be yielded
-cannot be associated with that [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session"). This arrangement is by
-design as it is generally not recommended to iterate an unbuffered
-[`Result`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Result "sqlalchemy.engine.Result") object outside of the transactional context in which it
-was created:
-
-```
-# context manager creates new Session
-with Session(engine) as session_obj:
-    result = sess.execute(select(User).where(User.id == 7))
-
-# context manager is closed, so session_obj above is closed, identity
-# map is replaced
-
-# iterating the result object can't associate the object with the
-# Session, raises this error.
-user = result.first()
-```
-
-Copy to clipboard
-
-The above situation typically will **not** occur when using the `asyncio`
-ORM extension, as when [`AsyncSession`](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncSession "sqlalchemy.ext.asyncio.AsyncSession") returns a sync-style
-[`Result`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Result "sqlalchemy.engine.Result"), the results have been pre-buffered when the statement
-was executed. This is to allow secondary eager loaders to invoke without needing
-an additional `await` call.
-
-To pre-buffer results in the above situation using the regular
-[`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session") in the same way that the `asyncio` extension does it,
-the `prebuffer_rows` execution option may be used as follows:
-
-```
-# context manager creates new Session
-with Session(engine) as session_obj:
-    # result internally pre-fetches all objects
-    result = sess.execute(
-        select(User).where(User.id == 7), execution_options={"prebuffer_rows": True}
-    )
-
-# context manager is closed, so session_obj above is closed, identity
-# map is replaced
-
-# pre-buffered objects are returned
-user = result.first()
-
-# however they are detached from the session, which has been closed
-assert inspect(user).detached
-assert inspect(user).session is None
-```
-
-Copy to clipboard
-
-Above, the selected ORM objects are fully generated within the `session_obj`
-block, associated with `session_obj` and buffered within the
-[`Result`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Result "sqlalchemy.engine.Result") object for iteration. Outside the block,
-`session_obj` is closed and expunges these ORM objects. Iterating the
-[`Result`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Result "sqlalchemy.engine.Result") object will yield those ORM objects, however as their
-originating [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session") has expunged them, they will be delivered in
-the [detached](https://docs.sqlalchemy.org/en/20/glossary.html#term-detached) state.
-
-Note
-
-The above reference to a “pre-buffered” vs. “un-buffered”
-[`Result`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Result "sqlalchemy.engine.Result") object refers to the process by which the ORM
-converts incoming raw database rows from the [DBAPI](https://docs.sqlalchemy.org/en/20/glossary.html#term-DBAPI) into ORM
-objects. It does not imply whether or not the underlying `cursor`
-object itself, which represents pending results from the DBAPI, is itself
-buffered or unbuffered, as this is essentially a lower layer of buffering.
-For background on buffering of the `cursor` results itself, see the
-section [Using Server Side Cursors (a.k.a. stream results)](https://docs.sqlalchemy.org/en/20/core/connections.html#engine-stream-results).
-
-### Type annotation can’t be interpreted for Annotated Declarative Table form [¶](https://docs.sqlalchemy.org/en/20/errors.html\#type-annotation-can-t-be-interpreted-for-annotated-declarative-table-form "Link to this heading")
-
-SQLAlchemy 2.0 introduces a new
-[Annotated Declarative Table](https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html#orm-declarative-mapped-column) declarative
-system which derives ORM mapped attribute information from [**PEP 484**](https://peps.python.org/pep-0484/)
-annotations within class definitions at runtime. A requirement of this form is
-that all ORM annotations must make use of a generic container called
-[`Mapped`](https://docs.sqlalchemy.org/en/20/orm/internals.html#sqlalchemy.orm.Mapped "sqlalchemy.orm.Mapped") to be properly annotated. Legacy SQLAlchemy mappings which
-include explicit [**PEP 484**](https://peps.python.org/pep-0484/) typing annotations, such as those which use the
-[legacy Mypy extension](https://docs.sqlalchemy.org/en/20/orm/extensions/mypy.html) for typing support, may include
-directives such as those for [`relationship()`](https://docs.sqlalchemy.org/en/20/orm/relationship_api.html#sqlalchemy.orm.relationship "sqlalchemy.orm.relationship") that don’t include this
-generic.
-
-To resolve, the classes may be marked with the `__allow_unmapped__` boolean
-attribute until they can be fully migrated to the 2.0 syntax. See the migration
-notes at [Migration to 2.0 Step Six - Add \_\_allow\_unmapped\_\_ to explicitly typed ORM models](https://docs.sqlalchemy.org/en/20/changelog/migration_20.html#migration-20-step-six) for an example.
-
-See also
-
-[Migration to 2.0 Step Six - Add \_\_allow\_unmapped\_\_ to explicitly typed ORM models](https://docs.sqlalchemy.org/en/20/changelog/migration_20.html#migration-20-step-six) \- in the [SQLAlchemy 2.0 - Major Migration Guide](https://docs.sqlalchemy.org/en/20/changelog/migration_20.html) document
-
-### When transforming <cls> to a dataclass, attribute(s) originate from superclass <cls> which is not a dataclass. [¶](https://docs.sqlalchemy.org/en/20/errors.html\#when-transforming-cls-to-a-dataclass-attribute-s-originate-from-superclass-cls-which-is-not-a-dataclass "Link to this heading")
-
-This warning occurs when using the SQLAlchemy ORM Mapped Dataclasses feature
-described at [Declarative Dataclass Mapping](https://docs.sqlalchemy.org/en/20/orm/dataclasses.html#orm-declarative-native-dataclasses) in conjunction with
-any mixin class or abstract base that is not itself declared as a
-dataclass, such as in the example below:
-
-```
-from __future__ import annotations
-
-import inspect
-from typing import Optional
-from uuid import uuid4
-
-from sqlalchemy import String
-from sqlalchemy.orm import DeclarativeBase
-from sqlalchemy.orm import Mapped
-from sqlalchemy.orm import mapped_column
-from sqlalchemy.orm import MappedAsDataclass
-
-class Mixin:
-    create_user: Mapped[int] = mapped_column()
-    update_user: Mapped[Optional[int]] = mapped_column(default=None, init=False)
-
-class Base(DeclarativeBase, MappedAsDataclass):
-    pass
-
-class User(Base, Mixin):
-    __tablename__ = "sys_user"
-
-    uid: Mapped[str] = mapped_column(
-        String(50), init=False, default_factory=uuid4, primary_key=True
-    )
-    username: Mapped[str] = mapped_column()
-    email: Mapped[str] = mapped_column()
-```
-
-Copy to clipboard
-
-Above, since `Mixin` does not itself extend from [`MappedAsDataclass`](https://docs.sqlalchemy.org/en/20/orm/mapping_api.html#sqlalchemy.orm.MappedAsDataclass "sqlalchemy.orm.MappedAsDataclass"),
-the following warning is generated:
-
-```
-SADeprecationWarning: When transforming <class '__main__.User'> to a
-dataclass, attribute(s) "create_user", "update_user" originates from
-superclass <class
-'__main__.Mixin'>, which is not a dataclass. This usage is deprecated and
-will raise an error in SQLAlchemy 2.1. When declaring SQLAlchemy
-Declarative Dataclasses, ensure that all mixin classes and other
-superclasses which include attributes are also a subclass of
-MappedAsDataclass.
-```
-
-Copy to clipboard
-
-The fix is to add [`MappedAsDataclass`](https://docs.sqlalchemy.org/en/20/orm/mapping_api.html#sqlalchemy.orm.MappedAsDataclass "sqlalchemy.orm.MappedAsDataclass") to the signature of
-`Mixin` as well:
-
-```
-class Mixin(MappedAsDataclass):
-    create_user: Mapped[int] = mapped_column()
-    update_user: Mapped[Optional[int]] = mapped_column(default=None, init=False)
-```
-
-Copy to clipboard
-
-Python’s [**PEP 681**](https://peps.python.org/pep-0681/) specification does not accommodate for attributes declared
-on superclasses of dataclasses that are not themselves dataclasses; per the
-behavior of Python dataclasses, such fields are ignored, as in the following
-example:
-
-```
-from dataclasses import dataclass
-from dataclasses import field
-import inspect
-from typing import Optional
-from uuid import uuid4
-
-class Mixin:
-    create_user: int
-    update_user: Optional[int] = field(default=None)
-
-@dataclass
-class User(Mixin):
-    uid: str = field(init=False, default_factory=lambda: str(uuid4()))
-    username: str
-    password: str
-    email: str
-```
-
-Copy to clipboard
-
-Above, the `User` class will not include `create_user` in its constructor
-nor will it attempt to interpret `update_user` as a dataclass attribute.
-This is because `Mixin` is not a dataclass.
-
-SQLAlchemy’s dataclasses feature within the 2.0 series does not honor this
-behavior correctly; instead, attributes on non-dataclass mixins and
-superclasses are treated as part of the final dataclass configuration. However
-type checkers such as Pyright and Mypy will not consider these fields as
-part of the dataclass constructor as they are to be ignored per [**PEP 681**](https://peps.python.org/pep-0681/).
-Since their presence is ambiguous otherwise, SQLAlchemy 2.1 will require that
-mixin classes which have SQLAlchemy mapped attributes within a dataclass
-hierarchy have to themselves be dataclasses.
-
-### Python dataclasses error encountered when creating dataclass for <classname> [¶](https://docs.sqlalchemy.org/en/20/errors.html\#python-dataclasses-error-encountered-when-creating-dataclass-for-classname "Link to this heading")
-
-When using the [`MappedAsDataclass`](https://docs.sqlalchemy.org/en/20/orm/mapping_api.html#sqlalchemy.orm.MappedAsDataclass "sqlalchemy.orm.MappedAsDataclass") mixin class or
-[`registry.mapped_as_dataclass()`](https://docs.sqlalchemy.org/en/20/orm/mapping_api.html#sqlalchemy.orm.registry.mapped_as_dataclass "sqlalchemy.orm.registry.mapped_as_dataclass") decorator, SQLAlchemy makes use
-of the actual [Python dataclasses](https://docs.python.org/3/library/dataclasses.html) module that’s in the Python standard library
-in order to apply dataclass behaviors to the target class. This API has
-its own error scenarios, most of which involve the construction of an
-`__init__()` method on the user defined class; the order of attributes
-declared on the class, as well as [on superclasses](https://docs.python.org/3/library/dataclasses.html#inheritance), determines
-how the `__init__()` method will be constructed and there are specific
-rules in how the attributes are organized as well as how they should make
-use of parameters such as `init=False`, `kw_only=True`, etc. **SQLAlchemy**
-**does not control or implement these rules**. Therefore, for errors of this nature,
-consult the [Python dataclasses](https://docs.python.org/3/library/dataclasses.html) documentation, with special
-attention to the rules applied to [inheritance](https://docs.python.org/3/library/dataclasses.html#inheritance).
-
-See also
-
-[Declarative Dataclass Mapping](https://docs.sqlalchemy.org/en/20/orm/dataclasses.html#orm-declarative-native-dataclasses) \- SQLAlchemy dataclasses documentation
-
-[Python dataclasses](https://docs.python.org/3/library/dataclasses.html) \- on the python.org website
-
-[inheritance](https://docs.python.org/3/library/dataclasses.html#inheritance) \- on the python.org website
-
-### per-row ORM Bulk Update by Primary Key requires that records contain primary key values [¶](https://docs.sqlalchemy.org/en/20/errors.html\#per-row-orm-bulk-update-by-primary-key-requires-that-records-contain-primary-key-values "Link to this heading")
-
-This error occurs when making use of the [ORM Bulk UPDATE by Primary Key](https://docs.sqlalchemy.org/en/20/orm/queryguide/dml.html#orm-queryguide-bulk-update)
-feature without supplying primary key values in the given records, such as:
-
-```
->>> session.execute(
-...     update(User).where(User.name == bindparam("u_name")),
-...     [\
-...         {"u_name": "spongebob", "fullname": "Spongebob Squarepants"},\
-...         {"u_name": "patrick", "fullname": "Patrick Star"},\
-...     ],
-... )
-```
-
-Copy to clipboard
-
-Above, the presence of a list of parameter dictionaries combined with usage of
-the [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session") to execute an ORM-enabled UPDATE statement will
-automatically make use of ORM Bulk Update by Primary Key, which expects
-parameter dictionaries to include primary key values, e.g.:
-
-```
->>> session.execute(
-...     update(User),
-...     [\
-...         {"id": 1, "fullname": "Spongebob Squarepants"},\
-...         {"id": 3, "fullname": "Patrick Star"},\
-...         {"id": 5, "fullname": "Eugene H. Krabs"},\
-...     ],
-... )
-```
-
-Copy to clipboard
-
-To invoke the UPDATE statement without supplying per-record primary key values,
-use [`Session.connection()`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session.connection "sqlalchemy.orm.Session.connection") to acquire the current [`Connection`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Connection "sqlalchemy.engine.Connection"),
-then invoke with that:
-
-```
->>> session.connection().execute(
-...     update(User).where(User.name == bindparam("u_name")),
-...     [\
-...         {"u_name": "spongebob", "fullname": "Spongebob Squarepants"},\
-...         {"u_name": "patrick", "fullname": "Patrick Star"},\
-...     ],
-... )
-```
-
-Copy to clipboard
-
-See also
-
-[ORM Bulk UPDATE by Primary Key](https://docs.sqlalchemy.org/en/20/orm/queryguide/dml.html#orm-queryguide-bulk-update)
-
-[Disabling Bulk ORM Update by Primary Key for an UPDATE statement with multiple parameter sets](https://docs.sqlalchemy.org/en/20/orm/queryguide/dml.html#orm-queryguide-bulk-update-disabling)
-
-## AsyncIO Exceptions [¶](https://docs.sqlalchemy.org/en/20/errors.html\#asyncio-exceptions "Link to this heading")
-
-### AwaitRequired [¶](https://docs.sqlalchemy.org/en/20/errors.html\#awaitrequired "Link to this heading")
-
-The SQLAlchemy async mode requires an async driver to be used to connect to the db.
-This error is usually raised when trying to use the async version of SQLAlchemy
-with a non compatible [DBAPI](https://docs.sqlalchemy.org/en/20/glossary.html#term-DBAPI).
-
-See also
-
-[Asynchronous I/O (asyncio)](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
-
-### MissingGreenlet [¶](https://docs.sqlalchemy.org/en/20/errors.html\#missinggreenlet "Link to this heading")
-
-A call to the async [DBAPI](https://docs.sqlalchemy.org/en/20/glossary.html#term-DBAPI) was initiated outside the greenlet spawn
-context usually setup by the SQLAlchemy AsyncIO proxy classes. Usually this
-error happens when an IO was attempted in an unexpected place, using a
-calling pattern that does not directly provide for use of the `await` keyword.
-When using the ORM this is nearly always due to the use of [lazy loading](https://docs.sqlalchemy.org/en/20/glossary.html#term-lazy-loading),
-which is not directly supported under asyncio without additional steps
-and/or alternate loader patterns in order to use successfully.
-
-See also
-
-[Preventing Implicit IO when Using AsyncSession](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#asyncio-orm-avoid-lazyloads) \- covers most ORM scenarios where
-this problem can occur and how to mitigate, including specific patterns
-to use with lazy load scenarios.
-
-### No Inspection Available [¶](https://docs.sqlalchemy.org/en/20/errors.html\#no-inspection-available "Link to this heading")
-
-Using the [`inspect()`](https://docs.sqlalchemy.org/en/20/core/inspection.html#sqlalchemy.inspect "sqlalchemy.inspect") function directly on an
-[`AsyncConnection`](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncConnection "sqlalchemy.ext.asyncio.AsyncConnection") or [`AsyncEngine`](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncEngine "sqlalchemy.ext.asyncio.AsyncEngine") object is
-not currently supported, as there is not yet an awaitable form of the
-[`Inspector`](https://docs.sqlalchemy.org/en/20/core/reflection.html#sqlalchemy.engine.reflection.Inspector "sqlalchemy.engine.reflection.Inspector") object available. Instead, the object
-is used by acquiring it using the
-[`inspect()`](https://docs.sqlalchemy.org/en/20/core/inspection.html#sqlalchemy.inspect "sqlalchemy.inspect") function in such a way that it refers to the underlying
-[`AsyncConnection.sync_connection`](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncConnection.sync_connection "sqlalchemy.ext.asyncio.AsyncConnection.sync_connection") attribute of the
-[`AsyncConnection`](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncConnection "sqlalchemy.ext.asyncio.AsyncConnection") object; the `Inspector` is
-then used in a “synchronous” calling style by using the
-[`AsyncConnection.run_sync()`](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncConnection.run_sync "sqlalchemy.ext.asyncio.AsyncConnection.run_sync") method along with a custom function
-that performs the desired operations:
-
-```
-async def async_main():
-    async with engine.connect() as conn:
-        tables = await conn.run_sync(
-            lambda sync_conn: inspect(sync_conn).get_table_names()
-        )
-```
-
-Copy to clipboard
-
-See also
-
-[Using the Inspector to inspect schema objects](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#asyncio-inspector) \- additional examples of using [`inspect()`](https://docs.sqlalchemy.org/en/20/core/inspection.html#sqlalchemy.inspect "sqlalchemy.inspect")
-with the asyncio extension.
-
-## Core Exception Classes [¶](https://docs.sqlalchemy.org/en/20/errors.html\#core-exception-classes "Link to this heading")
-
-See [Core Exceptions](https://docs.sqlalchemy.org/en/20/core/exceptions.html) for Core exception classes.
-
-## ORM Exception Classes [¶](https://docs.sqlalchemy.org/en/20/errors.html\#orm-exception-classes "Link to this heading")
-
-See [ORM Exceptions](https://docs.sqlalchemy.org/en/20/orm/exceptions.html) for ORM exception classes.
-
-## Legacy Exceptions [¶](https://docs.sqlalchemy.org/en/20/errors.html\#legacy-exceptions "Link to this heading")
-
-Exceptions in this section are not generated by current SQLAlchemy
-versions, however are provided here to suit exception message hyperlinks.
-
-### The <some function> in SQLAlchemy 2.0 will no longer <something> [¶](https://docs.sqlalchemy.org/en/20/errors.html\#the-some-function-in-sqlalchemy-2-0-will-no-longer-something "Link to this heading")
-
-SQLAlchemy 2.0 represents a major shift for a wide variety of key
-SQLAlchemy usage patterns in both the Core and ORM components. The goal
-of the 2.0 release is to make a slight readjustment in some of the most
-fundamental assumptions of SQLAlchemy since its early beginnings, and
-to deliver a newly streamlined usage model that is hoped to be significantly
-more minimalist and consistent between the Core and ORM components, as well as
-more capable.
-
-Introduced at [SQLAlchemy 2.0 - Major Migration Guide](https://docs.sqlalchemy.org/en/20/changelog/migration_20.html), the SQLAlchemy 2.0 project includes
-a comprehensive future compatibility system that’s integrated into the
-1.4 series of SQLAlchemy, such that applications will have a clear,
-unambiguous, and incremental upgrade path in order to migrate applications to
-being fully 2.0 compatible. The `RemovedIn20Warning` deprecation
-warning is at the base of this system to provide guidance on what behaviors in
-an existing codebase will need to be modified. An overview of how to enable
-this warning is at [SQLAlchemy 2.0 Deprecations Mode](https://docs.sqlalchemy.org/en/20/changelog/migration_14.html#deprecation-20-mode).
-
-See also
-
-[SQLAlchemy 2.0 - Major Migration Guide](https://docs.sqlalchemy.org/en/20/changelog/migration_20.html) \- An overview of the upgrade process from
-the 1.x series, as well as the current goals and progress of SQLAlchemy
-2.0.
-
-[SQLAlchemy 2.0 Deprecations Mode](https://docs.sqlalchemy.org/en/20/changelog/migration_14.html#deprecation-20-mode) \- specific guidelines on how to use
-“2.0 deprecations mode” in SQLAlchemy 1.4.
-
-### Object is being merged into a Session along the backref cascade [¶](https://docs.sqlalchemy.org/en/20/errors.html\#object-is-being-merged-into-a-session-along-the-backref-cascade "Link to this heading")
-
-This message refers to the “backref cascade” behavior of SQLAlchemy,
-removed in version 2.0. This refers to the action of
-an object being added into a [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session") as a result of another
-object that’s already present in that session being associated with it.
-As this behavior has been shown to be more confusing than helpful,
-the [`relationship.cascade_backrefs`](https://docs.sqlalchemy.org/en/20/orm/relationship_api.html#sqlalchemy.orm.relationship.params.cascade_backrefs "sqlalchemy.orm.relationship") and
-[`backref.cascade_backrefs`](https://docs.sqlalchemy.org/en/20/orm/relationship_api.html#sqlalchemy.orm.backref.params.cascade_backrefs "sqlalchemy.orm.backref") parameters were added, which can
-be set to `False` to disable it, and in SQLAlchemy 2.0 the “cascade backrefs”
-behavior has been removed entirely.
-
-For older SQLAlchemy versions, to set
-[`relationship.cascade_backrefs`](https://docs.sqlalchemy.org/en/20/orm/relationship_api.html#sqlalchemy.orm.relationship.params.cascade_backrefs "sqlalchemy.orm.relationship") to `False` on a backref that
-is currently configured using the [`relationship.backref`](https://docs.sqlalchemy.org/en/20/orm/relationship_api.html#sqlalchemy.orm.relationship.params.backref "sqlalchemy.orm.relationship") string
-parameter, the backref must be declared using the [`backref()`](https://docs.sqlalchemy.org/en/20/orm/relationship_api.html#sqlalchemy.orm.backref "sqlalchemy.orm.backref") function
-first so that the [`backref.cascade_backrefs`](https://docs.sqlalchemy.org/en/20/orm/relationship_api.html#sqlalchemy.orm.backref.params.cascade_backrefs "sqlalchemy.orm.backref") parameter may be
-passed.
-
-Alternatively, the entire “cascade backrefs” behavior can be turned off
-across the board by using the [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session") in “future” mode,
-by passing `True` for the [`Session.future`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session.params.future "sqlalchemy.orm.Session") parameter.
-
-See also
-
-[cascade\_backrefs behavior deprecated for removal in 2.0](https://docs.sqlalchemy.org/en/20/changelog/migration_14.html#change-5150) \- background on the change for SQLAlchemy 2.0.
-
-### select() construct created in “legacy” mode; keyword arguments, etc. [¶](https://docs.sqlalchemy.org/en/20/errors.html\#select-construct-created-in-legacy-mode-keyword-arguments-etc "Link to this heading")
-
-The [`select()`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.select "sqlalchemy.sql.expression.select") construct has been updated as of SQLAlchemy
-1.4 to support the newer calling style that is standard in
-SQLAlchemy 2.0. For backwards compatibility within
-the 1.4 series, the construct accepts arguments in both the “legacy” style as well
-as the “new” style.
-
-The “new” style features that column and table expressions are passed
-positionally to the [`select()`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.select "sqlalchemy.sql.expression.select") construct only; any other
-modifiers to the object must be passed using subsequent method chaining:
-
-```
-# this is the way to do it going forward
-stmt = select(table1.c.myid).where(table1.c.myid == table2.c.otherid)
-```
-
-Copy to clipboard
-
-For comparison, a [`select()`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.select "sqlalchemy.sql.expression.select") in legacy forms of SQLAlchemy,
-before methods like [`Select.where()`](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Select.where "sqlalchemy.sql.expression.Select.where") were even added, would like:
-
-```
-# this is how it was documented in original SQLAlchemy versions
-# many years ago
-stmt = select([table1.c.myid], whereclause=table1.c.myid == table2.c.otherid)
-```
-
-Copy to clipboard
-
-Or even that the “whereclause” would be passed positionally:
-
-```
-# this is also how it was documented in original SQLAlchemy versions
-# many years ago
-stmt = select([table1.c.myid], table1.c.myid == table2.c.otherid)
-```
-
-Copy to clipboard
-
-For some years now, the additional “whereclause” and other arguments that are
-accepted have been removed from most narrative documentation, leading to a
-calling style that is most familiar as the list of column arguments passed
-as a list, but no further arguments:
-
-```
-# this is how it's been documented since around version 1.0 or so
-stmt = select([table1.c.myid]).where(table1.c.myid == table2.c.otherid)
-```
-
-Copy to clipboard
-
-The document at [select() no longer accepts varied constructor arguments, columns are passed positionally](https://docs.sqlalchemy.org/en/20/changelog/migration_20.html#migration-20-5284) describes this change in terms
-of [2.0 Migration](https://docs.sqlalchemy.org/en/20/changelog/migration_20.html).
-
-See also
-
-[select() no longer accepts varied constructor arguments, columns are passed positionally](https://docs.sqlalchemy.org/en/20/changelog/migration_20.html#migration-20-5284)
-
-[SQLAlchemy 2.0 - Major Migration Guide](https://docs.sqlalchemy.org/en/20/changelog/migration_20.html)
-
-### A bind was located via legacy bound metadata, but since future=True is set on this Session, this bind is ignored. [¶](https://docs.sqlalchemy.org/en/20/errors.html\#a-bind-was-located-via-legacy-bound-metadata-but-since-future-true-is-set-on-this-session-this-bind-is-ignored "Link to this heading")
-
-The concept of “bound metadata” is present up until SQLAlchemy 1.4; as
-of SQLAlchemy 2.0 it’s been removed.
-
-This error refers to the [`MetaData.bind`](https://docs.sqlalchemy.org/en/20/core/metadata.html#sqlalchemy.schema.MetaData.params.bind "sqlalchemy.schema.MetaData") parameter on the
-[`MetaData`](https://docs.sqlalchemy.org/en/20/core/metadata.html#sqlalchemy.schema.MetaData "sqlalchemy.schema.MetaData") object that in turn allows objects like the ORM
-[`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session") to associate a particular mapped class with an
-`Engine`. In SQLAlchemy 2.0, the [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session") must be
-linked to each `Engine` directly. That is, instead of instantiating
-the [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session") or [`sessionmaker`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.sessionmaker "sqlalchemy.orm.sessionmaker") without any arguments,
-and associating the [`Engine`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Engine "sqlalchemy.engine.Engine") with the
-[`MetaData`](https://docs.sqlalchemy.org/en/20/core/metadata.html#sqlalchemy.schema.MetaData "sqlalchemy.schema.MetaData"):
-
-```
-engine = create_engine("sqlite://")
-Session = sessionmaker()
-metadata_obj = MetaData(bind=engine)
-Base = declarative_base(metadata=metadata_obj)
-
-class MyClass(Base): ...
-
-session = Session()
-session.add(MyClass())
-session.commit()
-```
-
-Copy to clipboard
-
-The [`Engine`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Engine "sqlalchemy.engine.Engine") must instead be associated directly with the
-[`sessionmaker`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.sessionmaker "sqlalchemy.orm.sessionmaker") or [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session"). The
-[`MetaData`](https://docs.sqlalchemy.org/en/20/core/metadata.html#sqlalchemy.schema.MetaData "sqlalchemy.schema.MetaData") object should no longer be associated with any
-engine:
-
-```
-engine = create_engine("sqlite://")
-Session = sessionmaker(engine)
-Base = declarative_base()
-
-class MyClass(Base): ...
-
-session = Session()
-session.add(MyClass())
-session.commit()
-```
-
-Copy to clipboard
-
-In SQLAlchemy 1.4, this [2.0 style](https://docs.sqlalchemy.org/en/20/glossary.html#term-2.0-style) behavior is enabled when the
-[`Session.future`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session.params.future "sqlalchemy.orm.Session") flag is set on [`sessionmaker`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.sessionmaker "sqlalchemy.orm.sessionmaker")
-or [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session").
-
-### This Compiled object is not bound to any Engine or Connection [¶](https://docs.sqlalchemy.org/en/20/errors.html\#this-compiled-object-is-not-bound-to-any-engine-or-connection "Link to this heading")
-
-This error refers to the concept of “bound metadata”, which is a legacy
-SQLAlchemy pattern present only in 1.x versions. The issue occurs when one invokes
-the `Executable.execute()` method directly off of a Core expression object
-that is not associated with any [`Engine`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Engine "sqlalchemy.engine.Engine"):
-
-```
-metadata_obj = MetaData()
-table = Table("t", metadata_obj, Column("q", Integer))
-
-stmt = select(table)
-result = stmt.execute()  # <--- raises
-```
-
-Copy to clipboard
-
-What the logic is expecting is that the [`MetaData`](https://docs.sqlalchemy.org/en/20/core/metadata.html#sqlalchemy.schema.MetaData "sqlalchemy.schema.MetaData") object has
-been **bound** to a [`Engine`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Engine "sqlalchemy.engine.Engine"):
-
-```
-engine = create_engine("mysql+pymysql://user:pass@host/db")
-metadata_obj = MetaData(bind=engine)
-```
-
-Copy to clipboard
-
-Where above, any statement that derives from a [`Table`](https://docs.sqlalchemy.org/en/20/core/metadata.html#sqlalchemy.schema.Table "sqlalchemy.schema.Table") which
-in turn derives from that [`MetaData`](https://docs.sqlalchemy.org/en/20/core/metadata.html#sqlalchemy.schema.MetaData "sqlalchemy.schema.MetaData") will implicitly make use of
-the given [`Engine`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Engine "sqlalchemy.engine.Engine") in order to invoke the statement.
-
-Note that the concept of bound metadata is **not present in SQLAlchemy 2.0**.
-The correct way to invoke statements is via
-the [`Connection.execute()`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Connection.execute "sqlalchemy.engine.Connection.execute") method of a [`Connection`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Connection "sqlalchemy.engine.Connection"):
-
-```
-with engine.connect() as conn:
-    result = conn.execute(stmt)
-```
-
-Copy to clipboard
-
-When using the ORM, a similar facility is available via the [`Session`](https://docs.sqlalchemy.org/en/20/orm/session_api.html#sqlalchemy.orm.Session "sqlalchemy.orm.Session"):
-
-```
-result = session.execute(stmt)
-```
-
-Copy to clipboard
-
-See also
-
-[Basics of Statement Execution](https://docs.sqlalchemy.org/en/20/tutorial/dbapi_transactions.html#tutorial-statement-execution)
-
-### This connection is on an inactive transaction. Please rollback() fully before proceeding [¶](https://docs.sqlalchemy.org/en/20/errors.html\#this-connection-is-on-an-inactive-transaction-please-rollback-fully-before-proceeding "Link to this heading")
-
-This error condition was added to SQLAlchemy as of version 1.4, and does not
-apply to SQLAlchemy 2.0. The error
-refers to the state where a [`Connection`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Connection "sqlalchemy.engine.Connection") is placed into a
-transaction using a method like [`Connection.begin()`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Connection.begin "sqlalchemy.engine.Connection.begin"), and then a
-further “marker” transaction is created within that scope; the “marker”
-transaction is then rolled back using [`Transaction.rollback()`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Transaction.rollback "sqlalchemy.engine.Transaction.rollback") or closed
-using [`Transaction.close()`](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Transaction.close "sqlalchemy.engine.Transaction.close"), however the outer transaction is still
-present in an “inactive” state and must be rolled back.
-
-The pattern looks like:
-
-```
-engine = create_engine(...)
-
-connection = engine.connect()
-transaction1 = connection.begin()
-
-# this is a "sub" or "marker" transaction, a logical nesting
-# structure based on "real" transaction transaction1
-transaction2 = connection.begin()
-transaction2.rollback()
-
-# transaction1 is still present and needs explicit rollback,
-# so this will raise
-connection.execute(text("select 1"))
-```
-
-Copy to clipboard
-
-Above, `transaction2` is a “marker” transaction, which indicates a logical
-nesting of transactions within an outer one; while the inner transaction
-can roll back the whole transaction via its rollback() method, its commit()
-method has no effect except to close the scope of the “marker” transaction
-itself. The call to `transaction2.rollback()` has the effect of
-**deactivating** transaction1 which means it is essentially rolled back
-at the database level, however is still present in order to accommodate
-a consistent nesting pattern of transactions.
-
-The correct resolution is to ensure the outer transaction is also
-rolled back:
-
-```
-transaction1.rollback()
-```
-
-Copy to clipboard
-
-This pattern is not commonly used in Core. Within the ORM, a similar issue can
-occur which is the product of the ORM’s “logical” transaction structure; this
-is described in the FAQ entry at [“This Session’s transaction has been rolled back due to a previous exception during flush.” (or similar)](https://docs.sqlalchemy.org/en/20/faq/sessions.html#faq-session-rollback).
-
-The “subtransaction” pattern is removed in SQLAlchemy 2.0 so that this
-particular programming pattern is no longer be available, preventing
-this error message.
-
-Previous:
-[Third Party Integration Issues](https://docs.sqlalchemy.org/en/20/faq/thirdparty.html "previous chapter")
-Next:
-[Changes and Migration](https://docs.sqlalchemy.org/en/20/changelog/index.html "next chapter")
-
-© [Copyright](https://docs.sqlalchemy.org/en/20/copyright.html) 2007-2025, the SQLAlchemy authors and contributors.
-
-
-
-**flambé!** the dragon and **_The Alchemist_** image designs created and generously donated by [Rotem Yaari](https://github.com/vmalloc).
-
-Created using [Sphinx](https://www.sphinx-doc.org/) 8.2.3.
-
-Documentation last generated: Mon 30 Jun 2025 11:07:05 AM EDT
\ No newline at end of file
diff --git a/attached_assets/image_1751378371366.png b/attached_assets/image_1751378371366.png
deleted file mode 100644
index 8e41c5a30fdf7788b72112cdeb47ff53ae47830b..0000000000000000000000000000000000000000
GIT binary patch
literal 0
HcmV?d00001

literal 454
zcmV;%0XhDOP)<h;3K|Lk000e1NJLTq002P%000#T0ssI2mWmYf0004vNkl<ZSPAV{
z%dNy945Tg><a%5U&2XU;R{jth5)x_mlB|OC^iW`I4>K5Ts?Q&(*4mytYOODa8wmL7
za03xqYm*LzAo&2KT<%flSrrEyfVA#XGD1q2&+x6aLO)Uc0zN@bIB@B#OXswrlJRoO
z3d-NVfKQMM4g-lotV@zVRI#G>L@?Ek?FhK@p#_jd!SCVw6n2tG>Iw>Ow~SXD_?XmA
zTtN2~=kD`(kU_RG9hPd^L78KzWy8Kz3ZcpXyPN|A_<f6STqwfy-#EA*3f~m>kIx5c
zk(**U6zsZ$!#zVAQ;cz={bc2eLm;I#0YF5Aa80}jE;2zMJh%4Ht1Fop%0}B(9NyX@
z#hQa;yx`yz#yByt&&NHz@Pq$R#}(Ya%cblt<Y>Mm8P7Njy*1XyCHNV8L`!C^IZr*e
z5Nx_|0ISO3%M@{8<RBRjIIP|{=?h{N%oOf}#7X_;>n?3fn_o<d9)S%<+q1(J8oX}+
w7*fm}ri>GZ1Kscc>E&<_js8f7r~jh<04Gz~k&C&;z5oCK07*qoM6N<$f@*Nh5&!@I

diff --git a/attached_assets/image_1751378374272.png b/attached_assets/image_1751378374272.png
deleted file mode 100644
index 8e41c5a30fdf7788b72112cdeb47ff53ae47830b..0000000000000000000000000000000000000000
GIT binary patch
literal 0
HcmV?d00001

literal 454
zcmV;%0XhDOP)<h;3K|Lk000e1NJLTq002P%000#T0ssI2mWmYf0004vNkl<ZSPAV{
z%dNy945Tg><a%5U&2XU;R{jth5)x_mlB|OC^iW`I4>K5Ts?Q&(*4mytYOODa8wmL7
za03xqYm*LzAo&2KT<%flSrrEyfVA#XGD1q2&+x6aLO)Uc0zN@bIB@B#OXswrlJRoO
z3d-NVfKQMM4g-lotV@zVRI#G>L@?Ek?FhK@p#_jd!SCVw6n2tG>Iw>Ow~SXD_?XmA
zTtN2~=kD`(kU_RG9hPd^L78KzWy8Kz3ZcpXyPN|A_<f6STqwfy-#EA*3f~m>kIx5c
zk(**U6zsZ$!#zVAQ;cz={bc2eLm;I#0YF5Aa80}jE;2zMJh%4Ht1Fop%0}B(9NyX@
z#hQa;yx`yz#yByt&&NHz@Pq$R#}(Ya%cblt<Y>Mm8P7Njy*1XyCHNV8L`!C^IZr*e
z5Nx_|0ISO3%M@{8<RBRjIIP|{=?h{N%oOf}#7X_;>n?3fn_o<d9)S%<+q1(J8oX}+
w7*fm}ri>GZ1Kscc>E&<_js8f7r~jh<04Gz~k&C&;z5oCK07*qoM6N<$f@*Nh5&!@I

diff --git a/attached_assets/image_1751456289717.png b/attached_assets/image_1751456289717.png
deleted file mode 100644
index 004f4555be06b870458093b2199f829042fe552c..0000000000000000000000000000000000000000
GIT binary patch
literal 0
HcmV?d00001

literal 2712
zcmdUxX*3iH8^;G(Qc`j;j1+Fys6^;y-?JubG7Q;wqlWBjBC;z)ZcP$nO@pzMZ7j_&
z83w}$F=Lpy_OUd)-gDni@8|c+`{8+h=YO6r&pFR?&i|icVQ$FBE6NK10QiiJ^sP=<
ze^OaITqhl|t|M>)Hn^3cE}#YnS~(G?ymapA004EFXMVYGp6Js-M)q(3;9SqYiwy%S
zcLxA?Zy4+A*n~OJXCwWsiQ?SrFd-i4UcS^afdLu!-^E@buVq@QA`&Ha$Jwmoga)KV
z1nNIaUU{BpY5B<ajBG+)Ksisv1$@J?w&7rJTz6Q#dcT|eZ8BI#`^5sFTYKS@Yi>{|
zZpT<mH^=mH!K!nI1b9<}33AXJBw{EawWn#NaYipB3QQ$0h<FP;kH2d9KgEZI+}yy~
z3SE19pzJl+{4Sjov<S^eNzoxS2H+8T>Nz<%=OyOI1PkeA&Cz^0=mVE?U90!*9`4fD
z-uL$R7ebijNMxNvn60ht`eALQMPyda_d$Zy$0pTL;hTBo+5FTQ70!U^rklO#$eNm@
zkx*mtz8}aktDZl=l8$Q06H=&&20ve$=k|VMpVE0ZGuOD?0<>gqIRd>|1gMi!Jb@qx
zzpA?8C8PoU&O!_XdA#3Ymj|z$w!^(H($hjwW2i?K?oGEwBmUGIlGD-EZJU)yJ!{7W
zfnobFm?Ep-G1e%{M*-m{Gl>>cWB%4Haf?EGQc*jCWML&i(={}H;VU+7IOWo2x<kRW
zP!;ocn$MitV_Z$}wS5QHZKoAKUH^(V2kvoZJfbYcKWq$?;|gb>Iy94a5y#cqI6_q7
z0l&WR92;oUtUtdJbEP`_%_BHTOF(rr5KbeYmyMG0R_>2ZE2xc$w(KQ>xvRwCNe;lR
z-AYj`vO}|M*Q+WMrC3cQ9@2AcqrXC4=lU%D8W)zED`}=n@DT$^mKuMCx^3;^1}>3F
zZ;g7h>`-ylqzxF0z_7{WU4w2rq}e|K?0)M;vXHZrR&V3i@^gB|>t?f9zRrUFQ|SWG
z8gMsd3@#rMfP~T~HFZU(-j7QKDR{B=7~dfX@e&o85JE1?D5hDamT-@6bmZPCdudL;
zng8MEO7ONBkDU*>K5+dl*oJ7GU~ng*Nig}yK_n*bPDAGT=J*<<_SYo!Hgr%53l*hz
zg*`+VjNsklV4{4Vq`Z{%rb*T?q6n*f{I+k``tq$czAHr2@2)}AfEpoqM6~haY*CNJ
z^IjGHWI-xXgHAFX=t_2m^~OI3J{804UXWB8C9X4P@O#*yT%L&W@<qOdN!rRl)bIS9
zp9}^I9o=uJoE*vQdHJA$UGYJaei*~VQSWVFE$-RnEBLzVr&<A!)Nk7Fh78Fa!ELM+
ztLtxX^SYH``~KKp-N^J<>@KlZGO@l-@!dW<qvU}VzT|=6ShR6|r*}wT%o6Hb+1XmV
zWjbCYOg{Y-gkElpwQ=V<=x^W>l1wq87naMJZuF&r%pg!6f9JUDPTbc4;JzoKM9BP_
zm+QBe&+yFs3nGdd{E;v!uQ2sHpHH1((Do~^pVt}7AN#-<vVpkTIFUkd;wFcl!qg;I
z*5{zS;xtFz=Y!`?{!rpK6lq?Tza48UPS&MuZThb+#_ltw8hM?V4n<y-2UDb~N;}rX
z_-czm*R&$+RSWL$!I6CkgU0EHNl1C{&gE~1$e|u$q{WQ}zVP6>me`9E4=EM4h=YWk
zsYEls-c-eTlhT!l@lO7$kP_k8&U9jMWan!w<uOrJSf@v5XH=)M*TYXc%eUbHvq$M0
z6RUo{=q1p!;ftAQ>gL6X>F|ryltbi?bAP#F_erNK9gpYJG`0xgJHy8~bY;~AR=<O3
z=9@2`mqbGJwk|{bouSFzPx8ho&*vZXpOIB>7VB}T8h39G&+@AMVqT<}fiBbCXAB=O
z%V^y0_9a>e_wQ6emgH8kY!McdJFGao%^^?rakojSsE^iALlVt7hVK25z#kkmtX_Fv
zOIft&$&J=o2NDCgR8(ScSaNE3T+t|eky(yEc+};)UOz@hdev{5SeIAcdc?V^2}rw3
zGK~8bO-&3GxdjI|uFxy2udVOvz&rlx!t%liaoX#QKAf8n#W)F_LQlQBDmJwg)YhIc
zxK5aIJ~EeT1*JD;0^Y<2de5OERlgGq!lfcdc3%Q~zfvnv<#<x8ro@l%7PYqJv&}_a
z+Q~uQE8gzdfgbXit8L0x1gmg8c6HGT<@dv+O;)qdfwWTC#ba1oGY`$em!<P=P9+M!
zm5(R1=5J!g6g;Bn!#zKrDnyW7GN6lyxnd#NK3wV6Mqo^_9k<3x!RMcu1N2%n<5d@1
z+tv_iZ>Tn-eQ|wdVs3saj(59iYCsL7>*&aXc)C-w75A|{aun;%)siRrhT~7!^Qrd|
z(QbNhecQkJ5l~DkB>~Z;d`kE6df0e4oZ%urnk^M+DN?D26jJ9)vR6b@qd(UP8{ZK0
zOufF}3L;d<k4p1$-23p~z#AQF{Y;lRh#lZ@eP1&8n&aUS&MXb2&TbDYIR1iYl^?b2
zd+Bu3Qz{>p=PSq|e(x_cOcnwtvgzmrPoTNIHx}e7)OYy@J|Xd%^-A2Z0GaN(k>zA9
zi{*SNc^`b>p48sH-!`?rMVlM;%9YaXSk<zBEvm^pR(<l6`o>O_8N}WjVxi<$Y@LxG
zlj$+WC5bIcBOEQRClez|+-E&OisOL|p&b!-S88KC1G#ZkT}II}v244A5euN{<>)Vp
z=ViK8So|ot+thYg(9a;&yJiu0T1xy?7ZLBi*iymu;GWEVpIer-AzTN0I%~>vE<&>8
zME<;Y`Xj{t1^dIZ2B--jb<ZkF!KI+ROY{zH<c`*p3z|R-)6i&Cm19;)Z1{HUGY%5b
zjmU^y7=FVs??bg_fNAwKmF>k}ugoHpnq;IcA<*~S-yoR*sDnZQ<1rdLeS4lPIO_<c
z78Hos*bRrFEkZ&X{_(k%08%3lSEU)a#nzUS{*M)ktM-y~vb33c%w)-)*YV~iCfz%$
z9!+pchRVfaUtdOf%*^LQz3llRZ43r&=<3QI)nU497QDG{GQSc0pNStBTuk5zKr}T;
z(}Z3e9MKM|JJ<A>&m=Lg=jyD8rKb6o4}{`~MQJC2VX6e`Uv=nIHqi=<=x{vQqw~0>
z&@)VA8Pl~BKXDQ+tPpZ~9!oc7I-(t7537-n`P-GtLKU~!Ek@p`V8hfuNRH|=ha2GD
pAGp72=gOQ_O6mT8#pSMJw!QvTkWpv~^dwCJj1A27Yjhta{0o^zOq2is

diff --git a/attached_assets/image_1751458059554.png b/attached_assets/image_1751458059554.png
deleted file mode 100644
index 252eaf170d98f708a6f3eeaf05393455669b5c7e..0000000000000000000000000000000000000000
GIT binary patch
literal 0
HcmV?d00001

literal 7407
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

diff --git a/attached_assets/image_1751460396430.png b/attached_assets/image_1751460396430.png
deleted file mode 100644
index 061fc4a704e27b037c14af51eacd5c3085b8c6f0..0000000000000000000000000000000000000000
GIT binary patch
literal 0
HcmV?d00001

literal 7523
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
YWCZ;+3*jTO9R(QNG1e{9dHDR_0Oz_I?EnA(

diff --git a/attached_assets/image_1752240455444.png b/attached_assets/image_1752240455444.png
deleted file mode 100644
index 536c2faad61ee854a5f15ef179022a678c69ecec..0000000000000000000000000000000000000000
GIT binary patch
literal 0
HcmV?d00001

literal 25643
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

diff --git a/attached_assets/image_1752739123355.png b/attached_assets/image_1752739123355.png
deleted file mode 100644
index fca225bbbd12a2bc5ece997c51f79903195ca819..0000000000000000000000000000000000000000
GIT binary patch
literal 0
HcmV?d00001

literal 6601
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
RMsB|VR1`Gj>trn>{sUNyOke;2

diff --git a/attached_assets/image_1752739324734.png b/attached_assets/image_1752739324734.png
deleted file mode 100644
index 27eb37a6f66daeaef92efd20b6d7bb88db18067f..0000000000000000000000000000000000000000
GIT binary patch
literal 0
HcmV?d00001

literal 3273
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
Yb@BUAbxsi0XYU8V)W{M>G;oXj2kY+{IsgCw

diff --git a/attached_assets/image_1753170001084.png b/attached_assets/image_1753170001084.png
deleted file mode 100644
index 0366225e87bf0e0ecf4db7064ad8b573b9a738a9..0000000000000000000000000000000000000000
GIT binary patch
literal 0
HcmV?d00001

literal 7619
zcmc(EWmFtNvo;oV(F9*0xO>pxO9F)8A-E?%a9e^Ce8b`d3l=PBSlrzfcMtCFu6N1%
z-k<k;KfgX_q^Iiibk)>!Jx|wnRb@F`Yzk~7BqUq~d0BNNB$RN(y*Va2;#^!>$cVTg
zJFCmdAQcY+w-FsQ3uz^3B&5<voI8_eh(4BsypA&x5?=e$feg3L`-Fr<rL7<<{lU#}
ze;!Bm#Z)HOl2Pljh^dqm16#*0f)yw<nv9r+B|1-SbtLfzQ|-w9U{$dNoYJ|7@AJX=
z4(lN=Gt*3nty6WHn3%h9nCe?v>?Q(2Iywmgx?^d;udT3^BM0>0xk?WTG6x4KLbOMi
zRj<yx_<Z41|H9q&gf+NY5{-Zm(dOTLJvX95v`ll}S44;h(FTBUSw#Cp3Zj2fnCTpr
zf9kvbU*ev|?+{2{UtU37cv%_WhYuez>gp)ML~!fCyTR1r9xVd{&#0-X9UL7$PE`Q@
zjS0rmD%L0V8$sgcRuO*vnl+=lwN=_c(Y#y-YogkIF<AL42u+Uj=ImH=Y8L+Q?Wqg)
z&BPgcLM=lud>|G1i~aokd}+sH;^Sh<o6E~;j*5WGr^&13GQoJ>-QQgE^HfD;Wo2PP
zBiW_vi|_YLEEy0MDl58HFVtz``94bQ5Lw~6fBxvY-RD{nk`WDoKtwr3wmRFhm=6sa
z<W72vY$RtQE-HL2i^l0C#ffjru}ygL`(_n)bORfSLg*@|8vsSvMqz&vCQOs-n!QQA
zE6Gd>`N!4uSf$%S*C7L69&p@w;EZ&1b&^g$T&(4gHOz{Ox!%2=9V^ZliK;`6%_1?K
zn97$AGYIW5voM?Ra{_Z#CigkI<tVrbYVH{4eZYBFVgP+riSYbcNzB+zO-)T6V;yPP
z{U^j25gDaOMz(X|CWpQuG^UH~80m*HZ|5Fc8yvoZ#lb+y{_uMub~)>DK7>m)Uo!h`
zkF=|Cjx<K?PsQ}O%<{haX<V}%k)~T{Ohgcxp?lu=>-7<@VdPK$61B0T(a`xC%PI~b
z1Le-H92l+BnOk6ZS}<e!Mw_utQ06{+DF+v&Lg6Moofm6WTxig58HOUmux>S{+iSPY
zWE96UQKq{xZvn%f#yZt(Z(v;Zy@9(rj&2r{spru<yA%@7#uDtLXO+M~nZVp8^+;NY
z+g_{VnGFvg?oN}QlnMEmbCUGe7bc*_1g}Ko)H1c_D|Z*LY8z6*$`_R7YZJQ9M+F;#
zzq;zCt6)`e8ia9LOl1D3#Tr|j_P;yTJH83KPJA$_(q^gB6QrT!@BF*s#sSrVGGv>^
z={IvMxPJBd!Zz8=t5k9ACL4OgtgoJsj1zrk0*sjOmPUAboUy{p642={i7&PCmmA7c
zL3tf66AuyFraU_FKqawTgzoiIT^T!|Je)Zef6f_1dK40*6S;p`_I%O*;<WN=C*?CS
z8(8={ora$C4wP-)Wm=4wCYJ|p>F=bgjgskyC69i}m5`e}4=!pf?QixPcq_v{RkQ#6
z4b|8w=)#V6oy50?f4>1nyy>67)xL;C`5c~8EZTqPMOs$wx*U51;E0uej~n-9u_0VC
z2hehRymGa@XhK-El%vUdE?u2Qi&_}pk#Sk}m3Kn!$C!|b!lbYl?a=8_g0_e!K?8@L
zpz8`+XxbtKl!Ou8(2M_F@e%iyU4V?A765Njw8M0#vtds0rl3z(?;C|YyE1hT#M~=C
zI!uSJKs!~~q2W`C4)ZJvS54o*kD@q!kd}9~J_9&4W*5JE0Yy4TgaDjo(A)UD_D9^d
zD#m1veU^yY>trQ)(LXi{2;UmW)-Q~0Nph3qJNmvkx_(qI9#t|T4AFQO!)Ic2X9v)G
z;9|On`HYK_m9>-#ev2jbbD7)Nu7>%Q`E!7|<kfAwR8pk+XR<n^!y$rJRlF3@WQc9E
z?D9RBZm&B#kNjISsHm<5W1zjcA4|PzLYXXtH&{l$#9f3Nlb<xAa1owtFPp(4pf?Uz
z*E-Z%Z0q;weTUX`%(d{!zEjXW2k)8uRWVX5N#pm76Mf(uuj9HA&#@GcD0JN;u9iSA
zp}4D4OUQIyxYDPX!V>&USjUDa{rN5KQ3Z#+)%bf3GP~<A*EW;}U{t8LP9_0$K<zq~
zyecYq1ex%|W`}WJj%9{pNJ4#HbzVXW?_%FgFN`6g@Z2dRR+#&b{$gQ|N2`l4Ar|P-
z|B_jI=NCCyP`=@eFe}C-TDMda4!azCA!32bgIZ1vvwT76THc?wkFK*2PiwBjg25Dt
zdEbZZ(or24G1hy+^$g1eX^rjycIGc#x4mTFHitjne`!lKNas4h)gpo={IWDAK+)qn
zF+^h^6#0YxZ7yhTF^8Sy<1Pi+l7&vOgPclq`U!U&<WlqnDF&_ZaM{~sHjp+l*Ccp3
z6R1$Q_;wFQclPDa&1Oh*ko`wDlfP|WAo?{Vf0e60MMb^EYI(z0rYDLSYx*rcW}SG1
zXzwu}IHZE9US*{?RUCRMR`PNCXO)-HX=&*}FYMR$=W}mF5>iVTKFBDI=wb&blD<LC
zXLeBMazJA${tMkJ3c!WgZ?w{Q*mznna=m>-4N*5eZY`+hnGl5uOlaWP_7MNQL|MSl
zh{JL#!@mi=%H3xz%OAX1kbeH&oREPgf%rLmDs;)p0J*#&Lk(@lCq5J4GKI46#%V5@
z$L0!C;ha_*^)b)4O9;c&1kNYPz@ZzCR?n;+p+~1DF*4+&$>(;V?`Esg!D=8lT9C=p
zQ)gZGr~5XXxgpm@Nyy9SvBm%9l%9@mXN?WVk%N(vRL{vIKkEmHvF|%+(8d>CJF|UK
z3U0W`%!zPHL36@LAaoPk?}+&r?T&z-9dO$`m+7jdNC#mJkbCE_+(2`OI>9xU{H!~H
z^z0`;1Ga|1F=T#$V8<mPnR3rbXq}F%AWN%12y!b*+^V)J3pwwY-r@n?Hkmj;X~FDG
z?8I=~gdEk54$G)BZ#KY=cm!$lLTI+MTB0^hfq#jEBU;7%hROU2QIQdMRUA|iM=g6#
z0~JO{NVZ)VTkX8C{kzy_F?d{<fvlq`M6dVA9T`WCs%-=Ooh5=g#2D;I&!?TRn^8_N
zJq9VPhcvC+*;45pMgIIAy;4*9QA<WCo*Rm4f(X+w9)rP_NqiyW0nuY3-}+ImLf<|H
z19ZXSxxfmHxE9?a**gQ`b7*x~p(TSP-eFM~C{4E^Yp!dS`_+XfGBkM8R5J{C{F){@
zE7#b^iA+N8(w}d--FlM3izLlY@{Lma8u=-&h?U?2Tsa93#A{#5yh75mUVc3Kljuyd
zO@|*SQ+iTP>~J%bKuFF{GuHWF_#nFgoz_ibnJp!Y?UbN{HwD9P8bg{Jt!g{I<XdBJ
zM5YIC^Zz!Pz4hq7TZ56U78G?2uTvhoPRR9&ypK!@o?68)o|ii**iJ<ueIV8-muS2B
zARfwBwK6mkf0|Bx&n*u;C)q&HqtreAD(d(3x84O+ZCUn6>F1>sVP+!c&WE)0>GxA-
zQAyh4?0CMRB>N4cT}V_gc@Bt3h(Kmz?$C2-LynUJA*7$;k4*lVED+-xX&bW9c*ts>
zAjx^+-G!YZrSnSwAt4xDB>Bic<vy!}$}iv+q!;6_>HK>+RUCijZp7mG@#TJx{2fDS
zl3)FP?q3?tq{LjMB;(<@b_R@3uc!iPlX~L{t##dvQqH}<$C=jZnBDRZ@XU|5=N$)D
zNvO^T)KgU#7aI=DwKGdv6Qx)3CT6?GU-&^AJ^J?FJ6O#skIrx=Hce8cv(4WQ@Xx=1
z*Y@3qFa>BI&X)M*6jd%PxEk#5Cm;gg&)Rw_NWS}J<+wT%!=FU|b3>Yy;a(nalBzCB
zY64Be+M@XN%P<|*M2f}IYNK`dARN2D^VGAdrNo2uqpp!~tV*38o>$B{b=NdowT$9y
zDeA$O4|3~*qz|7Gf6x|vAs|}L5%5zRJ*yjPVVU2r0fRN6U!&4(HI098?3*<&#H$U4
z&nmbIC1aO<_6DLio0}D+Fzw-Lk92>ttVB$;sk+2K3onqCE;A$<Zwgjm|K~c-ZzNP9
z0s4o1O@DZmkhjmu&@^uQ&aOy(lQZ6#88)lKLYH5L{$+Q=>;}D1?#M6#9z%Xwx*-J;
zs@ZNqfB)zV<SJP2a*8gQ|F)Ty98_xNm;FMd+6lg0;>R<70bng8<n>|2!#9e&%$o3&
zdcw<ClKoy_AmFdoO3wM$L8|#*XhwhLvHNcc>M!q5p_TdC<$_Hnzj|_n8y<S$EEJXK
zl6y0LG?P&8uBPCrX0{jX0+{Z-_;0=^ozM5y6}5K@kwrQDqP@FMX`-w~m%!as(Artg
zeu))J$PXkFb>}PD<cSavqRyb=dF4twyCu*qzZk?FAJS??M}xrrRx%F*?-zJ*8a0HL
zh(*4hjVevsixa$E4ho_vg8Ms2y174Nbrz&&)}pdBZF^7=qV*k@T($Sb3usPYzH8q!
znLlytj-2<uk6RgA&LXf8HcNtQ3n#^e^TGx}OUrdHb-u+WwIr%#yvxtm=;@I<)9&-?
z19h3Q#grZfmKA__p0N7}eUEjU-W7)<cZ{Sr4hRNcP1WHx)3mCs`&iZ=qz_Y@0@I!h
zqO>lDbB|l5W4|e>MRG5rtT!S{+tvG4!65=w<nmF*mvMMc5Ug1sec~h-@glOFEE(0y
zIXY20B02!|*VuMOJ^9NB&4mnu@qZlW40Ei7-HT+hbH4>+go(lSqXlYdA6h%rSmZcw
z%P;*3K-+Zbfw1ce&_NJYu7Z$kMhHL;*YVHZw&6N?(+#w;1_tc?EOvqS3?;0b3vs|W
zYY%u_j7bIlksNW415CpMGO=m=8Xcd_4=#e&v~E1AALc%tY!zzBZUF<0!5!zrYJ~3+
zTxYp!Sty7@jS1C5@UYrSi}<fZ*ohyq+HCOj;|#`vw=Sa^PWA4zFJ}=D>7%Ru|Gq%?
z-D>HlJ!|dqawPE3G0(R8@g)prNkFjeB7pZ`>3d=jw^X_JbHR-~gV|vH4j&@?fudSS
zbgZi4GGU^2O87v1IWO-VUt?sm_C`8&$UyGg@tkwj#)m2wB^98B09Di)g`dKzXfgEX
zKYm2bDN%mZx@hLGdzzCd<{{R$<{~m;?P#@yeb>BRg^@FzN#2OE_=ob_y>C)*$%qDl
z;Ii%A8v8{lU#7{9$P`Z))=+0Y@ETTkmaiIC^rDaJjd4FgU*_8~^{7u|AqrW^%Eq~^
z*?EpU9n5q}hOD^Me$jZW+Er{5Wp!U_!@(_U3kVP?YrDF@bZKYIw-tAbFQBVZ;Uv0+
zjsS=!tt75A{l`d&y(W2%_QG~!PB`M(v7XNn{8b$IN&74Yzo2HSg+-*|tQCm=?YDxI
zjjkQ&zdTnf1gw%gP|2g2tY#*dRTVG3_Jme7FAwIIww92T@G*piEB4O|ZuirepdnSB
zbvv#CZ1%~r+C`cPPvAw1>2ag4`JN9|b#oj6V={Y*NY*DhDazNMSoKa_5kd-hiTFYi
z`xF#$k9Ak=C+)NdE-DupgDgw3L<}FyotMdK2vJ*T?ruR4?K8_*9Vv5^B-{h7YP)$=
zjWOw=iCsD?E2|%_6;GPLv(d(9#7}&^Smi%zk<{YGuNFQkx^UYq3bO56s?-O){ruzA
zCLuFIOZuNqLUJ;c$YX1v=OXq$^1cDsKkLU`Yg^jeQHAV&u%g=Jov(4C2-(ejm>o%T
zrNRUu+%O)mub;Afa1gq(!j7QK;VUadmX&r08r%LKTCSTe(bTKi*OvelX89fT>2=RV
z%vZeL3+wN$o^<b%GTwR1AqM%hoKfk^ZJ=mS>qPwdGy1yD=-{Ao?*u;v1_qe3M{V@v
zq4RIGJt(&x86V#kikj8&Z)~dHoS>`^nqWON`2TJyP@Pq*g~X(Ml#7Uo4=`L4598d9
zsp|T#_8$<vxNt-6vBU>`@ksx|IIC@BK7CMYt#zI%^fJ3EjN;t3e0D|H;7uiUbLuUX
zheXLY2t@vPIHW5S3NI0IU!;QdL;v7d3uwL_uQ;~68{M0qPBJKnr~YL<GhNG0$tgaX
zy6J)C`AAPf!)&D9ClgLc+{Gk>Q+*<=Bn}Zg;2{nYuGr<_;*mx~9V_+Cuy)ohaqdvu
z%EN{SX~vf^ytO%XsKL)a&gY<`L{(Yz+x*YJI4m$E=6!WID}&_n!x345T0Aw&v(AEt
zj#;JcMd4JQloptAW;5_%IhJ5yNP+VyoGT;RABn~<@qdhbx{MG_4(qgt@@^Dr3%+jM
zAWPTFEulX^5ko(3;Z~4bx}DGO?jLKll_Kz3Z>$XZGTm^`QcPx_QS*`}S2-oH)mj+E
z16e?5XjSU&@`_i4DoLcdb^2BTO>Dz*26m^fON>&H5Pi0PDKAziZ5b;%cPH@N-B&E)
zTN#H<q)OXEmIzCc8$bokO-AD}(U$+LwnzTTg!_V8B6v^Hqvq;a+tdUbOv^xKcF<tD
z_VOhj4Y!n<l8V^&B2EPOw#JcK?$j^JBE7?`%%&mGdvULg9L?C6CO3F(aj$kkW-#I~
z%FY9Y);p~CHvQy0ugMk5VrVT?6bx@6FcKCLk8qe0EO1T`tn##*v|?8)kwjj6sKF26
zcREtcNA<eBl`a&+8w)FQ&W9|<=T&79E~s3<&33Z*!l#@Z*K!-D<2WMdB|Hti@KtoZ
z@tdjch(g#9cq%arR=qEsW`-Gj4z_afkt@S-XspLaxv6VEz94p_o>~^^rqsfly)r=j
z_sI^fWsm)49>OjU!`hl*MfAB28>32R-mYw!@L;J^Yc@Q6r`D2O?+nMwPUB|XyPuwv
z8RZ+sC*7)NqzdiFlUQE&ooX(==1k_w-&>A+G_OP+SPB9{e!;||B=9kmH&<7<D>1zt
z(V4!ldRkJ1FfzW{NXTrnf05?v!_kJCa;#^L9YLNhrR^*9nbP|sP(t!Uc&=ee__kR{
zrtiM{-eMdofCwJF3_@$^;QpG~V*Sc6O&p6bz%%1)iwjt@0;QFv)9*=rh3))yjnOB-
zX8%(^IsKqYF>GwE)>UZbvgrK_PdrlTd}a>}fd9wzaFijI2Htxe069ZI5+qFtw5s&l
z_Cs#%>c{G(xuOp#?^>qZ7u+0|fgNI$*pHF`9{1}Okd3yh7+&%i)lq-Z7YH@yY$+d(
zWcE~^^a8~H4DbO>S@x_0Q;dI_oK!<2YEujibff(+B%huT%6}b1w-#c2(SQ8zwPo-6
zb<Wb;mea)7?CRq~IG=^Wj}BL{<gx(aqz)NtB-Avvt6y{W;vkR%Qjpo^h)7*7jH+t`
zU$V7%sxhVr-GwEE$8;7Dc)5!uBxD~(Awb5$Rtng1a3ShTc_ZcctvtEV?GT$;jvspz
zFUv;$l{w%vI7EA6<WMJ!+zU5%Xgf}v%?`PhyUV!7UZqrt-=Lm0mdDctNi5DgOv)vY
zrdN3*Q@glOBC?{w8Z+#tAMd+)k4#^;S9Iw$!JzHUxRL%GI6mzVEr34_U-M#l((~o-
z_f%N$29U6;(m^(__axq)Kv?J87Lwzn(zKWsIF(qAR7<$8R+(z;!t^z%-t+XTlJIQv
zh`+PW>|@aTi<7rTLTc&M#Yb4g%Nl9fmT*vry@^}cdcEqb<5@Z8$U2=-e&}-<rXiV6
zc{yozP4om3@)8vJ$5Cl~fVREFjH7MV3)&0M<1LdPH{tNP9@L4OXU6ZZq=Y9PVQskd
z7Ozh05oyDt!zE&POPmB6&)xo5<oUg2JP{RGG}LjRv+bxUk$KXa|CYW?WjFQLluqQ@
z7V2ZX%8z1)u?w^?p<l6CcIP=Pf6m|f`tG`I1~z#&c9tr>;p`ta;6}Zpku%yAy}ikR
zL4-XFR!0WbNiU4`c5nm3sF8;-9tJ8a!hep{Imh1MQ3kYF-yUsieV6ZRL^qq4x5=S;
zgDEzn8rgU|C^H4ZpH~7?IyQtB*2*h}!8enujlULayW9s0#}G?OUX4$!Q-p>hrTZb%
zQ3&Q%HnTrv%R=V|@`giQtzTupqZ1lP3o`OKEGk+pk#Hl+ZVcTjY#{PRkU|ahn6Kb7
zW!S_dyKn0P%{eB;a>y7)1RL#6Yu%!t)j(~&7>M_F;7#J=qDLmfsplPhu}LnzAx2w=
zwJn|bMiMc>7UTdz-s@+9(7SM5@v;?(_6_mob`b_5XVQj<XJ%`m^}KyBMCO6$ZV@)R
zuAoZ#N?EvqhJX2m8#P@DvDLRZ;5c{y3{Orpx$Uo3NO}SBU~q45hoHp5(Kq%6kJrf6
zb^>XtdV5&_<)gETVko7|@1d^jZuzinM}X&#SLZM1?I|la=8A-S?Y4QZavvX<Oi>ga
zwyqe};v}~4#jWOkj)NM!DpvS|m>u$1kTf>r=*5Pg+bMIp4_c~RLH6`K_NA*48~<uk
z!y+=PG}UTqYBN_8E~Y#6+pIGBm)0yznTEOr;5YJaEI^fm_9iH9LbBDF$A^jw?1l5i
zW4)jpE&(CuQtm%!5`-pO?4&i%=)EBHPI$jFw7o+!OV?QNQe8Wuj*N5kDh?qQOZ4&k
zuf6+DI^t*Ftx<^vn=V=qaLMQezzI(RF-_*hd98Zo$;K{U7B*Q=_EK$HUFWi@*xUcg
z?r`cH-4Og^Lr)2}y{=U-*BA52Y)Z|hIHqNm+YEMnF9CsqcdZA8cmFWE!PKg>7QP=Y
z%U6XbGf|eNr0Xq6Fg)mLS<r`a8%np&ETkT{NqarS<;C8NlX-qd#L8fib6DCSdrQ*l
zOm~@2+^va=Dz{WBMW<)r1Z3fN*0=g||Af_Ndx{F3BUwo`E4;vr0Dpp$X-%N=TmWj4
zNcZ4-c(nh!E6Do735hJ_*w8CRIhBNS58w@beaUW%+NZP{03bOyf!=F^^>f{7X^XMg
z$CzN6SoP>@XC^);_TjIiPzZU^g2Lur$P)5|@T~^j1+{pDnOwO;k0Yk}5c#vkI%y82
z-IWD=?*gB$;ypV|pILma$+4+v;VA+d{!o?mrMhaMIH!s-a8ePPJcChk=)e?_R{U1V
z&DWADoTXPJ7~pL`QkAy#F#Y_iJ-u~R#NS89+s%(K3eC>ksKltiGmY!+L1BNR`XG~y
z@!X~5ysFg+7lvNf{vK)bS+zU$gYaLijmvu@IJ4~VQXxiMxQ|4{9vyxdo-=4c)Al~k
z^K<rVbUhz_Hg_MDn#YJU@mmKLjmbl@!Q>m`;g>WuELpjS5A~y)R5Ba2d}ly0pi@ah
zBcfU{|2-@Bb)=my=FTlfLBBB=D?jT4rJCCAboT8iz$}PRzzNL(S|?yzm|qY$GeSJ@
zaIs#h^eq<pTo%17!*)k)Xroq;F;WK=V^?&7%;c^;zsJ2I)9!M<%e6|A#&>JWWwYDi
znu@G`5p+8dYW9&FdBC7aGPz^31D>)IX#smLE34#gDROOVacmj%_Gul=L-1wl$So-{
zM2F4_H~8rxT^aNrBZBOiK9!Z|=<3NR5a12c&Glb#;k=9)f<F8peX5^1OK*<ZWsHop
zBG1<!!=I`Qbl)KFUzQ_-srN}J1jK==e<e;K2xvC~QhoyKd8d}u!&^(k2G`u!a>v|H
z45scANmP=QIqHts?D=0O#{*=Rgsf|N1dH%^!AdUm37PS^YVN4rP+HYTa6N^9dAXBh
zK!ccpUZ0)y)57<T{4DcfAVG_aoa}Wj9#(kFj6RM=r4Cl)KL%is00`&f==CI<VVrjd
wYyUGSb1=X$9bwS_Y2g3E{{Nq1)T_s@FLzH#**Z)Rg;yknH_EcbGRFS@1CSo6<p2Nx

diff --git a/attached_assets/image_1753171981306.png b/attached_assets/image_1753171981306.png
deleted file mode 100644
index 8548cfd8e70fd0404e906a32e2798d4cb9f27b38..0000000000000000000000000000000000000000
GIT binary patch
literal 0
HcmV?d00001

literal 7504
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

diff --git a/attached_assets/image_1753259924788.png b/attached_assets/image_1753259924788.png
deleted file mode 100644
index b5e60170f1c523ca807e2d64c6ad0e293f6187f8..0000000000000000000000000000000000000000
GIT binary patch
literal 0
HcmV?d00001

literal 4058
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

diff --git a/attached_assets/image_1753269586287.png b/attached_assets/image_1753269586287.png
deleted file mode 100644
index 80708713ff0cdf22f8657f5369ee9b22f9483161..0000000000000000000000000000000000000000
GIT binary patch
literal 0
HcmV?d00001

literal 8392
zcmeHtRZyJ4wk{A7+}$O(69{gB!7UIh_%K0&OK=a)FhdBz-ARHC?hb=%@L+>O2yTNN
z_PKjM?Yg&a)v5h<9=f`E)$0DMS9N{T{l~o3QpUrk#zsLw!BbTM=%S#YqCPcj%ok4)
zV(vAWrw-LaS6LpVVvKh0X@G9~M)M5{3M?My4*2|OjOD8G-U9^%zvmwf74A~{0R@H8
zOcn4(&&T|58P7;xznlNKBNRgsij)t^-6(Yd78bDP2^6nk%#%$jm$3x!8x%l`L|4HD
zqUOBYHhvqU^sv7Ivl&{6zenPgXxU*(6GWUkw7jF*T#V0k#lz|E#MoU|>n;~|69`Zx
zH$yKTrN@2e{VUt97BW0$gDdMUL#MS!<N!m>@M~2-tQ969CzMI9P%IK90YQldhz0%U
z0&jcFvA{*Nv)uhs3CyOCX5l+$umhFvsmE{yBGh9K9?wRzh2`Wn+&4I{h{PTh%5v6S
zcv=VDrD!aYDTT)T?y*4_NNOQSO#ss+=m4C+6bO8kOth?7L4aEDYA{yD<31`?v=e4D
zqz>)RjOXgpLtl}&CRUOlZ;4WpXJr2<ylwlmO#W9lyN{)U)2X_Jn(+$Q+3?j_czy%y
z+pe>H*IzkKnT0eL4V;;%FBji2Q3yco`q*;vNLkn2wN=>8H19ko1Nd`Xb#ZAgYg#D4
zxiCVW5vAvI;M+T$7f?!dS08>aqi2NIA%ZSdK?zXSaiy2SKEj`SUkbBfnczU=^R5d3
z8tJWQEn}O^)=IVQS`*P?EVip<no!SPGOrGp9Qs7@jhoei{U(+ATQ}T{uiH!k;Tyi2
zGUUY+R1u@rpBKg{f=+=V)EBpg;GK-9+5*M-T8=R{8K(TLniU`D*H0rknIXapMb~V2
z<Ix*g!m@OIR<mw*F3cqeLR~93cmu4k|M$~K!U4}{q}ghy!Wg!%`EXT%{MZ68X@hc%
zoAC8JoCrBi<SKjmz--pQbvKN~)<mK#Q2xYD7T&7BdMZ+>IGe=SI~I%;GmX&|z(%7^
zGK(mLy{IsHYg$c*lcOT974NFy>qvO_C;uzIaYUy5j;ETAjnMXUllR|^Y~GAAAFO{W
zv@x)nCm9F%TJc1ts>2+7nRT^kxP@crQUFDX&!VBqTcg<aiEvk3&KjEyvjavzUwD3p
zR7cYA7cZr6{x=(WUr=}1<}Q+_-%yJ5GSF?d2Hi=XsQp261aK7hYV<T#xyBvvjRF1H
z-0fd*D>`toEASk=Chcj7Ef_S7JcbuD2RwKg#py?<|BR5&C7xZw)Hp8fg=*u+i291u
z2VtKF@t|aNh!{-XSpJ!cPiS%DB(?!qE`HX#gM+MKjr%);u^S&gAjt2m`<PjHwYxqG
zTD|ck!rrGTbqMhtQJ=553T%Zjl9K=k@5BY=d3gJ5o7{7&zsFX2&FTSSV|RSYx={-X
zm5$~~9fNOKS$I|Z-2)w&7}#-o2|C`6n7+bO*i?HUQZZs4vo<K5UbAE6Q84e;I%!cc
zCJvJ$u+Z@3(#W(Gd38yx<Dpky90C(efF8DvLUU+=g*mxf{peDgWTP0f+E>g+mLgvI
z7?z8@C<PPmS+$%^z!BrI+{OcTJfuJ!hMJssLDqBx9c0j^I;e*3J9wMPJ<je2|JSgT
z(XDtaa54h2nXb%3FRW&tdUVmI!#=v54suS%N@TVaDQ2Bc<xFKG|1<RI6P8v`G7fjQ
zfb+KvDvg)qz*}dcd`^<(11+37E?zMKOk8ku_yQO5)MW>aK$+=BypF2^=TtVN|GbDs
zXM&wPhVw|iL#`nNaZy7g6Zm|p{R|$bIB1l^QBgg*9o9(%q@>X~vs+)vq<@o2%xz54
zBJ>3~s61PeKSN9u>b9cPWJ&x6WIq#D{;8qP4pYtx3$S{-xu=GE<Ff9_J#vEUD8ZLL
zA*6epQk0)2!;I2qI$2xJr@X*haC|f0%*3!u27WYtBt{g-6V11I*5*BfE9HvfGue8G
z$dD}Oz=-m+%7gK7r?ls)nL^$`xv6@|oHV2vN{I{w7w&k;mm}!%2U|7{0j*PV-fLdX
z7p}kOR$(@E^V)2>B<4zs;(-ZW7+KcCcbTo4oXu=h4uY~x2Hu_1>W?}B!W7T*SIs(f
z|EQRS77b_7JGf^Ea_mHzxAn$Pr>P7R!eJe|+nrVlFM1gtfRn5~{CMor-Gr~ZInl%f
zRQM*knHVcRj0Z+5U>I7d1s-n8kO+z@h4-95jJ~FZ8+zSt$Ud~)jDC98tHFXxN`eeZ
z2LANrTk#$9z3XClmz{XO9Z<;13S6&tJU2d;>ZdMhj#361dv`Y9YL<!H@%$_xW*2Gz
zIW$j$42deJiXb?haS;eHsZ*FLy|QlqXbU6W4=P5|#CSzu**n9KXJMPz6_34ZbX6vb
ziS>&ZL1w{2E$>AjiVV30{3ByzeQ9^MC~irj-cGp;X1td=z^IfQ4pH*D==7bByJGe*
zXLZ2!RHeY-VSECmBaiTWIU=)7oKMbsj8{Hn-?owi9#QFykWDF5f_h8MmW%^(b(A)_
za8X6;9b(*ufAN#59<hLico}M$-SHwrXr^0Kz25aKQ!rClK5<z^-w{4xg8zSz*8cy<
zvoot|30Nsoyd>pT;RX7-SeT3x%AHiDC;aetP?Z|8cc2DoT6Bl8_=rr6QPwngY*Fg&
zumT<4&ECgATg7R(35LHvF%|=abpeTJu1;aHLn@Kd)Ra%12us!Q7y?l1^m$3qy#8DV
z3NsfZ1Lw#<31G6y3^Q)2vZ`eL`n!w^HIdmsq31PWkehmEOP*r=Lg0ZfD0?w884FcM
zr&_De$_BvqO6D=Gf@vjB<#!|TG?Ma+xA!ZI$eL*VSZI4mh5?{?uCSOK@H8o%91C^0
zgld3>EOG&uH!xT>^GzxTvPm_GiA@hVruv!i4#*>F<kb<g2U2>;!9qQ4_?Kets`=EQ
zABbGK5xZHD(nzO``gZTW$5g1H)muy)NJbj`E&7Ir0BCsX7#<a;WH*vA05d=gG_z<>
zGhNEspiW`a9qgCEztr!3gf|nNeQ~dv;&8Qrhu$YqlKpWQq2Xy#@fD%YJI%~i?-?{P
zmi5X+a$v;|dr%Wav8HYHpQ>$^n?UGZ!>fW{vz3ojcm`Z%bP&Lt+%HE#_Omzo6hQb8
zWZ>tGHMujqJN2aBp$gX|o~H8cvti9JjIO(zJep<dgD!~5Ajhli(wS+~vHYU(w9>X@
zb~gT_UqPaG*1QMR1%Q@TZGY>4M)Io?`B63-C+pkVx#H-XEA?}$CEH2|q8{6i4(+q*
zb=A^8Yj>4xJ|47BgI%i3s_m%wBOTs79w!+X*i7;b&eR1iT9tT?x67*2AP<@r8SqVB
zmk<50=FKjRfaK)yJ~+h8*J$3Jj+WHs;dt-E^FFdJHeV0Wz>Dsylue`_6nWb;_&=Q{
zT`uk#)|GLA44f$_D)0gWlIj~Y96xgZnvK3$b%u$>UbX(f3uIC92cCT#Wvg$hJ=5oP
zJm)%8e{VxFE@+y0ivjhRo1>%m*tl*FG^Bm>ft8EF)!mmm+RN~=YLz6{jaqzn46C#<
zkSdLrL$>Zp*#`ip6Fyj$B{%o4G4tkSchv`Hi<!&)X^|6CMCOL5o$ZCM{h{e9an+w5
z=(_|;Q9TX+D4?m)6PZa=a9p-g&F^1H8AvTrunD@H;`9x4`6XB?8z8+tWi*fq9ZjpR
zQ;>gVf_9svj<gGa^;>(jWo6-?+L#f7M?i+o9t_Y+dEf=O`}g33)i@ijug{mpOk>wK
z72Z<MGPtY~KG0G$M*`(l_)ejNRJI02eKWr{fg{vxBahy3=OiZSNGrz=7MXX8AIq%d
zc+3Ns<yQNp*l=H#D_+ksCuejY-;Tte>=*^Z{iXt8qR`4<0437ovw%}}BlfUU6u!Kr
zD(6s~zNe;iN6^XjTPZ^XrufG58gEu?C`FesFMdxYzh8z+gXuoF6V=P;f*gtk3Vsy4
zY^4LQ_)*n)@e!b^dy$`3{OLucicRVTQ*#rbwY;ji;OmO;$anwEzhVm2cYx(;)Qa9-
zdHf))(EEv4$uA@AWBr)Qm)=#)$-{6a6jyEY$~P78_Sakt5g?78MajGC!{v7p4$)}O
zwzxsIrsW%~;tE95Ij4n&s#afkw{<ivS(JD_j@6`#w@7WP604&jL^UxU6Bm?jEKLz5
zCw>{^he?uL(Gz*4dt!SsU$yOvyO0Ipx#_<mjXxvTS*Bqe8s>Q&xSf+wQtI4>fIY0q
zx(@8Yq((Tr5i=^*4rI;v@^`TW2^nc|SUZNmeYJwY=?V9fX7Usy!rW6yAqlZa_{A)=
zCm%;(Xi{U2;YyKtA4r6juRb9uM%qT;+&7z!`ZLyallFD-dOYyuT|$GeBz7G4my5G|
zF+^%vK}}<<F}EoxC6i97jXFHtN$d9K70Ii>$;VJ;(#Fji%(@pZOs06Bg(Y$D3BRCh
zyS4fqjf%M905#=peJ&z=zU)%JCMGan-zbdE^Q0;$*pO`v!eoIbc19ru>H?N*E5?@H
zE!uI)*PGv?GdmcTKi^UxpoZ2YCah&e!j%<4y1(>B1~2=nZsWR*Bc-Sa&M>f$LF6{S
zM8B@$(uaMy9y_b*kS5C`s-_`<9M9Jc{7iiIs+8-*dejc74U`Qp|4x%|>8rr-%{@<O
z!KlU@9g6!qmf2A=2zwprtEKoUmJF*#F|j0XYYNtLEh2__e?cXAI%W9MNx}Kxfsk~0
zLJNvdJuS=N&&p${aeQF>7@fUrjGqzZvT;9V54TKe>PqO^t^v<R%=BOc;T@ix8*BJA
z@IO>DckhB$#ar~lQ?8u)pPLStGi4w2H-<3EPI4lzZDdQVb)|<rz7{hd4f3j#KIxsx
zoVwTzG^f60tWbkds98*n8$$F5w1NWCG4Yb2^Z2T9E$gDKxBE=j9*Frn=1fuS<I@?h
z=npV4NQh6oL_zlw-Tpobd?Mnp3kGuLe1nMpr`dFvB*4)(w1~Qg+w18YZXi8_*kT4|
z{x+4+Nl8mt+8?13#7njmHyB}>wp|vO-yD6M!=LMWCx9MpsvRk(NtXzmfGPMF6z&<b
zpz~SO4F{WZ$gefS!lRATOB}JH<#vC;e1li_PX$pI&@531@9)kf*!Q!|F*3S}m>A(G
zA1Fdl<vw{2G_xP%mVq_5rU<`;g;WR21H9wQ!m9a1lGGLCI&jvIOfQ)2U%bb#Hgn`k
zi419d*4csr@HX@~$Q#CZ5mV~<kKO*8ryhgI=TfJPlQBPjVj8n>UZb~7!b_<XIef59
zx<JF$3J$vy+N#p0!pYJ%>$*YHF?U5xIpD4=hh1c}$TEwrPsuVi+m+w^-QR8wFEA0=
zI4WAf+<g{sbx3x1u=xlr4SwC8xnvxj*%syY>JgU@3GFVs&f7(Bd_V9LhdQgnSHV9f
zes;{icVklfvrI_;$nrT5;>iUC4)^nXyZ#+-fxtcGz6I_J#peYptUh0vM~*HtOpfP*
zn+gOy{plY~1d4-SW!#VPh3c2oR6Q6G^p2*5uXT3$?5^MpAPWhXBUj>s6IQ2fABjZX
z8J-InB7P}api6JY<3lwM?7ayp=37s8d$BNs1W>}F@Isq58Rk7JFqF^-Dsg`(1#K$@
zY{}wzuoLt>Q+QFZbQ5f;yxxXMyycmp1SwzreNS{RCE@e>a6EQ-O`3~qa<FpJdV=x%
z6}120A?lb>YaK9Z7?RP#WZ4;rd97#*3KgQku6|%xSu(>^M)%EoR0x~sdzVAhI#JMK
zC#q-OZ!LbiF(>BMj1%;_s>GaY_!+$)ikK(1?RiI2X<bMEtjnihz~50k`88%feUvso
zFQM(CvE3EAs&5vrwq16JW5Gi@=5J%&K`}L4lX~OQUPK+Zf8w>=LobMVc4O$rwCCz*
z{Gy%16@#0NZyg-(m9CUGSAH|116a98WSzx{U9PXAu>;yTm%+}I;voD7c$`JgjieBN
zP!pbm!6T8&!=8XdTuuZ#2F+@L8539I_4?jjK$b*INNMCoCBMUACG3J`nIXa7BaF~4
zbw^rs@w{PY?|G&1PcN!#O2osVss{c+B*KZoYIqX4h&3kVx?xc}Q`6C$Ia_H6+5-IT
z!doeB-{ni6()Zdi3C{OqJoux1GlzcIHu2p<6dheQ@*3+QKPLwqa6@1i&;sTTc<p?%
z!<`aDsOgv2zN!Q|5cLiQIQKuTlC|#5e{AOVU64Qe;rs^2e0Qd%J+zapbO!gTwSgv}
z(xO+<7o+B^qw^?Rh~s8Or<hwZ{>=PgSATW-b$yV>Q(bI+BaCN)2MB%Wm%(95;I_L7
znao`&^e}(PZI`3j$-5t)d0HDT1k~(hs^5A(zqyUiZFP{n(={0KN3O3&Nu4NO0>2Gc
z5r%rWu5x~&lE7>`$FpCiv-?-kH+$ss5sw~1Q<tV8%E~%;(m|mo+8X_q8$Hs`C0DwW
zNuYZf^qs$+$LGoNG45zHSjk2*J&1NFT^;049X)W%Nc*9ROtK!=E`<0;F>=`mqmA?z
zSLZvggh;RYcdG08jkj+IIGr+H?<W{J1VbuT!`<^+a=$Z>yJrwi48}hPOB~Fo>Y0Od
z5~B|t^O4K1QoY-#>wk*q`TFH0rrh=FnCaJZBfUI_4wiBz){v3?Loq>G<wEGu1J>!(
zXpU9H{|+PnjVn)}^I*1zcmwd(egYkJh0f`oFbfs6Pbq^E90ykKnuO6c4yCUn3IX0}
z|H7@#o(+2GP^p`!bAWe5nSV9@Q1iSVUu8L+WG%+|HfC7J?oQ#HxX#tN#5^wF{SvCJ
zVdZ^kuyXUCoPlkuj_S;Ti}N>f1<*lqIge*@_AtUPKYmvBJb>!xU*MuL&m}T3a(B=^
zv(uvJd=HL9A!?HK`W-_GCuG#6RR~dWdrIjOM34U8!2hithr(T{UQIs@E7uWy;KqO(
zuAsXU<=VA(liI&3%p_}FR_U`OJOL?uW)m<FMnU*uGdWJ^QHLx$S|_%wti`9@wW5yi
zQc_ZJy6&PgGc$g67A;<Q<iiBam?OIjHBmZhPVd3to!+ZtU~ZhXqg9gZ?6T`}O?`bb
zHMMq%cZbKr7MET;g(AaSAhhb|j2C;;8K@I#zJ7kOLytJp0}kfe_ARE=Xw~Ard%CQC
zSARe9<jrc-Z+=M8YOna^^>o@c87))zvKFpfUVc#5YR^9PEUF)VBGe-^SG^{ulgq!Q
z9skU8>V(osQhWR&ADDahuep%O%R_01xIND1!GgSZ!l34pAd4=&_%{;u*jvTV6mz2{
zkP+)lb$Fr9H{0Gu(T9xVM^iv^b(w3Y6e;8%VG9H<*;I!2*?i8)$o@n3^j)q-W%V^R
zqUx!uwL<-|ZyTEr8fEX#g}d2Vtq!OHAtL2I3TVuRw6C_KgUp?O(H+CXeZgO90W!%}
zf!k3kyNTd$c<?xPW_zr^l5A`1@ege%KlNScF}+A_+hc5*`(JYSNOk9!K{BxmR0d3_
zq9Uk@IIcOc5|!eVCNKqa%vnUZ`zkgUFbSw26R9kYyuu;^^BoKS=Wf{OoAOA{S0_b8
z+BxxNnGbb*$t*ULIvcr`d5ESGPG1lk&7js&Nr3qtM`?MpDJ@(od>Dii)4u^0L8vHl
zij2nh2pw%bOB)vW=uXQvsy)}Dcq!DRqMjZ#AZQin)}FYDeBFUP=o|bppKeLQDSls#
z_I*k?eCrZ<R#edGW(8>Y3PP)Jzq{rH{YYzH%I%YWrEYSSCCIN&X77OLuB)2sn6e!Y
z5>Tg+c(8WbfDxCMl>sD=F$5VJ-bnsTv0q$C>1*Bq)qWqov|FA1CVTo##B&k%Fexj-
zDr0&v{o2)OcZAnC4F)k_%=Y#vygc6zwznPTSzKtvTg2JuscXE#xv*LqOR38%F#L$|
z<NUqZLUMS<ICs3iPuyk8XbI$SF8aoJ2gtD`Wk@4o{p4Opia)0CF2-~OmC-e_WN%=A
zlTY%+Dk!{TLA7?)veEAA!!!K$;MG<3F-0Ekm6A3Q#mrTQ$6v#}28%1!g2waR@pU!s
z7sH6(2<AEp5%Afs2JdeKkjH}hD+gHM`@H0be51#$!2H6Szt^5c23-(&%Y5=H#K9u>
z@aBg8VFL-EFZH;&-cXP9@Ra`0yNBJra!cG`PXBP4oFFH!oYQq=h>)_`Cmy8qW3hlY
zh{g1FS*xVsKHE@Hp!LV$jIE<_a#;X>K-pkPsoNfj&Ph~9nY9_Y5Yj_Two*|&honNf
zZGpL%ZfUhF75`MM_+gNUd8|Dk&?;-^M;=2<X>LP7)EZ=Bsif_dl&Ocza?KI=RGdK)
z?e8yYUSvYkV!wx9LfC_3kp8ecc5Sg0X04O+JY5yygALmdTQT#M3s_)^hn#GCL+`cg
zW&xQ|^KTy(+uY6;%+;Wsd9|H&;bvzI|1@f9H~#LJ4LM6!!p(QT1PExuJNE-JzbwCq
zf2$FL>h*y-%qux?-p!RTzdN8W4&+a}>UWmf3v$RKz{Xa)n90KPJ@t)~szXbFsj4g|
z6Z{m*>7U0@OtG{k&L$eY+Y2T%i=4jUm{7l4V{cI-6K$b4_?ar=zqez>&p~YV+5JP?
z!TGImgT<Xq-zhO133&b6)zX#o;pmQWvOyb;nxUho!?*G)P|#5WQz`P76tjSO84ZJ7
z*|ejVj$7QIL|GN}{SJ^QLC)`mJD{lUgDn%k81_c<Ffk6#PRIj6lsMfx%zT?W{?-#c
z>4s^fX0J)`(r4Mn3|VL^JMy$(o2h*Ex>qx4;JpkP&_guu&w~$-#g)t@i!!CPE{Y8c
zU=45zRv8@V=Ocou%N)PyZp9oJXt5+>+N5wDc7x!ieGdOX1PFpk6VA8}hra0;90%FV
zY22023MA4~MgtLCHfK0J^U)>@p?(pqBoCMYD2O4WgCBu^{fWe$5zfO?DUyPW<DVRF
zMQ`Xh0nkySun}MVnZO5*x0YAs9vUEwp=5b!jdo1Zol7kwIZyC8rLN4tYixVY1*mMX
zKhpywNhOswWX0^D-i9xWSH7Xd>g4T$U}h%=&$ti8$vv%g8^!~+4fwgaf<2uffDq{~
zIm`58J=+^Y3QfSobdRs?N@8l0qt${10n(VSy&KK&M*nfh!NDVH^EbS*(Ox@zuV6;y
zrrXZIB%bM_{8Oq`G1GoOnGniG{>VJciLBZy3i!J;9s7OFJvNKvGEo%;d6#a<cNgU`
zKVQ+#R5Hi>B#?->1oQn70Sc79N3|(yy56g0lJ+|xypC^4u>1)r<Imh&IvC@!PNcS_
ziDEk|loHO5g-574>Qxtg&Y&S}w9jp+8$I5rc`TWP7gs&T^a|dn_;SA!9@C^uYkV<`
zv10`cZzZFEOnf=HcG_oxA67|zpoU@6BwzJaG)-bs3(~&<5g)6@D_&RjzHNJN_R`f6
zUBdQ*l_3DVU))TUZi%4V7H8{n5_#A>(d6KkJNCF;Ti`Jx$P+^iIW83gKx33nn~Yjd
zelxb!dXH#mG=7cTV=5pbt)o`<(*($uVSTLWf2l`nxLZRrc{pfvJ~x+qC%7dF6syvc
za;a*K*lmPR(r1L%7F6fozLa@QHvJ_M-X|CVUNxMEE~#?$A3fC0&-lFo>KSE{wc?ie
z!&XyyAfwA;Oon7MQHG66+Vg=Ad6TL#0;q3xj}B)Ot-X9aFG2^eDIi`B;$<^d%ZtmQ
z`ydslafI_xA#=M!k>G$`PiRsNY*Rz(p`MRi^g-vE?BuqY%a~5<)`>63vC+w6DOoP;
z!}C)QAc_h-Tk4j~QDeuS`Sz3XhgE0k_g)T)NpB3<Q3}J-=J&~Bm2cB5_qk>hZqE}L
zDu|bHTo`_zTG02kT9n@ti#r4|$#t#~0v4u@yFb^>C!|5l5HQZ0k5V<|{9lRLsh_0)
zG^dfx&o(Xj;e%oRU+Qihj5aOIp;$fPUr<s_)?&x!>YPBBZws!Q>z)4K|I<W2iQPNs
z?T;T4Ip6XOG=dah0cPJ1-9KXfLlue8bhK9UHhGN}|5IT|c<z^Wd@McuQd#oPv`>3e
z&t>miiRLOTM3dh-7?=f`wPuqc57eAe)D?0;aQx$w6D8HbtA1kpQ8hI+polj@RI|a2
z4-fUb?KpitYwr3;niW2Az3!=JVr9-9dQ5=ycPEd%sEEwK$9r+pR=XM5xs|-Il%Urq
z$4_te%So*`jJJ<-6=q?5r(wHjsZ=Pjc29D-!n}>`^OK>pX#Wum55$ony9b<#<UZwM
zpl(s3w4m(UO%gIPU+p|yO)%S={SO}E@va{~O!oa@{Gdkl^!EXZs-hO4Lf-Q8e*so<
B@RR@m


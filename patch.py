diff --git a/app.py b/app.py
index f0a319e22046bf84fbbe5fdd224acce1393e4c8b..c1414e29d2bb61be2892c0f538fb51758d4cd9aa 100644
--- a/app.py
+++ b/app.py
@@ -3283,86 +3283,86 @@ button.st-emotion-cache-1h08hrp.e1e4lema2:disabled {
             for book_info in all_books:
                 book_name = book_info[0]
                 if book_name not in book_board_map:
                     board_name = book_info[1] if book_info[1] else 'Not set'
                     book_board_map[book_name] = board_name
         except Exception as e:
             # Handle case where all_books might be empty or malformed
             pass
 
         # Convert to sorted list for table display
         for book_name in sorted(book_board_map.keys()):
             table_data.append({'Book Name': book_name, 'Board': book_board_map[book_name]})
 
         if table_data:
             # Create DataFrame for display
             table_df = pd.DataFrame(table_data)
             st.dataframe(table_df, use_container_width=True, hide_index=True)
         else:
             st.info("No books found in the database.")
 
         # Clear refresh flags without automatic rerun to prevent infinite loops
         for flag in ['completion_changed', 'major_update_needed']:
             if flag in st.session_state:
                 del st.session_state[flag]
 
-components.html(
-    """
-    <div style="text-align: center; margin-top: 10px;">
-        <span style="font-size: 12px; color: #888; cursor: pointer; text-decoration: underline;"
-              onclick="document.getElementById('dont-click-modal').style.display='flex';">
-            Please do not click
-        </span>
-    </div>
-
-    <div id="dont-click-modal" style="display:none; position: fixed; top:0; left:0; width:100%; height:100%;
-        background-color: rgba(0,0,0,0.5); z-index:1000; align-items: center; justify-content: center;">
-      <div style="background-color: white; padding: 20px; border-radius: 8px; text-align: center; max-width: 300px;">
-        <p style="margin-bottom: 20px;">Can't you read? That clearly said not to click.</p>
-        <button onclick="document.getElementById('dont-click-modal').style.display='none';"
-                style="margin-right: 10px;">Go back</button>
-        <button onclick="window.open('https://youtu.be/dQ4w9WgXcQ', '_blank');">Proceed anyway</button>
-      </div>
-    </div>
-    """,
-    height=300,
-)
-
-with reporting_tab:
-    st.header("Reporting")
-    st.markdown("Filter tasks by user, book, board, tag, and date range from all uploaded data.")
-
-    # Get filter options from database
-    users = get_users_from_database(engine)
-    books = get_books_from_database(engine)
-    boards = get_boards_from_database(engine)
-    tags = get_tags_from_database(engine)
-
-    if not users:
-        st.info("No users found in database. Please add entries in the 'Add Book' tab first.")
-        st.stop()
+    components.html(
+        """
+        <div style="text-align: center; margin-top: 10px;">
+            <span style="font-size: 12px; color: #888; cursor: pointer; text-decoration: underline;"
+                  onclick="document.getElementById('dont-click-modal').style.display='flex';">
+                Please do not click
+            </span>
+        </div>
+    
+        <div id="dont-click-modal" style="display:none; position: fixed; top:0; left:0; width:100%; height:100%;
+            background-color: rgba(0,0,0,0.5); z-index:1000; align-items: center; justify-content: center;">
+          <div style="background-color: white; padding: 20px; border-radius: 8px; text-align: center; max-width: 300px;">
+            <p style="margin-bottom: 20px;">Can't you read? That clearly said not to click.</p>
+            <button onclick="document.getElementById('dont-click-modal').style.display='none';"
+                    style="margin-right: 10px;">Go back</button>
+            <button onclick="window.open('https://youtu.be/dQ4w9WgXcQ', '_blank');">Proceed anyway</button>
+          </div>
+        </div>
+        """,
+        height=300,
+    )
 
+    with reporting_tab:
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
+            st.stop()
+    
         # Filter selection - organized in columns
         col1, col2 = st.columns(2)
 
         with col1:
             # User selection dropdown
             selected_user = st.selectbox(
                 "Select User:", options=["All Users"] + users, help="Choose a user to view their tasks"
             )
 
             # Book search input
             book_search = st.text_input(
                 "Search Book (optional):",
                 placeholder="Start typing to search books...",
                 help="Type to search for a specific book",
             )
             # Match the search to available books
             if book_search:
                 matched_books = [book for book in books if book_search.lower() in book.lower()]
                 if matched_books:
                     selected_book = st.selectbox(
                         "Select from matches:", options=matched_books, help="Choose from matching books"
                     )
                 else:
                     st.warning("No books found matching your search")
                     selected_book = "All Books"
@@ -3372,51 +3372,51 @@ with reporting_tab:
         with col2:
             # Board selection dropdown
             selected_board = st.selectbox(
                 "Select Board (optional):", options=["All Boards"] + boards, help="Choose a specific board to filter by"
             )
 
             # Tag selection dropdown
             selected_tag = st.selectbox(
                 "Select Tag (optional):", options=["All Tags"] + tags, help="Choose a specific tag to filter by"
             )
 
         # Date range selection
         col1, col2 = st.columns(2)
         with col1:
             start_date = st.date_input("Start Date (optional):", value=None, help="Leave empty to include all dates")
 
         with col2:
             end_date = st.date_input("End Date (optional):", value=None, help="Leave empty to include all dates")
 
         # Update button
         update_button = st.button("Update Table", type="primary")
 
         # Validate date range
         if start_date and end_date and start_date > end_date:
             st.error("Start date must be before end date")
-        return
+            return
         
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
                     end_date=end_date,
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
                 'end_date': end_date,
             }

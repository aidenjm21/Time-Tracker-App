diff --git a/app.py b/app.py
index 8351bdfc9a3f4f536417e06e8da79b92d243245a..3396cdd1e1c26f613fb5d88290a73a428c96ada9 100644
--- a/app.py
+++ b/app.py
@@ -3282,50 +3282,71 @@ button.st-emotion-cache-1h08hrp.e1e4lema2:disabled {
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
 
+        # Playful hidden popup
+        st.markdown(
+            """
+            <div style="text-align: center; margin-top: 10px;">
+                <span style="font-size: 12px; color: #888; cursor: pointer; text-decoration: underline;"
+                      onclick="document.getElementById('dont-click-modal').style.display='flex';">
+                    Please do not click
+                </span>
+            </div>
+
+            <div id="dont-click-modal" style="display:none; position: fixed; top:0; left:0; width:100%; height:100%; background-color: rgba(0,0,0,0.5); z-index:1000; align-items: center; justify-content: center;">
+              <div style="background-color: white; padding: 20px; border-radius: 8px; text-align: center; max-width: 300px;">
+                <p style="margin-bottom: 20px;">Can't you read? That clearly said not to click.</p>
+                <button onclick="document.getElementById('dont-click-modal').style.display='none';" style="margin-right: 10px;">Go back</button>
+                <button onclick="window.open('https://youtu.be/dQ4w9WgXcQ', '_blank');">Proceed anyway</button>
+              </div>
+            </div>
+            """,
+            unsafe_allow_html=True,
+        )
+
     with reporting_tab:
         st.header("Reporting")
         st.markdown("Filter tasks by user, book, board, tag, and date range from all uploaded data.")
 
         # Get filter options from database
         users = get_users_from_database(engine)
         books = get_books_from_database(engine)
         boards = get_boards_from_database(engine)
         tags = get_tags_from_database(engine)
 
         if not users:
             st.info("No users found in database. Please add entries in the 'Add Book' tab first.")
             st.stop()
 
         # Filter selection - organized in columns
         col1, col2 = st.columns(2)
 
         with col1:
             # User selection dropdown
             selected_user = st.selectbox(
                 "Select User:", options=["All Users"] + users, help="Choose a user to view their tasks"
             )
 
             # Book search input
             book_search = st.text_input(

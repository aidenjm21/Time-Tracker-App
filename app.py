nt = conn.execute(text('SELECT COUNT(*) FROM trello_time_tracking WHERE archived = TRUE')).scalar()
            
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

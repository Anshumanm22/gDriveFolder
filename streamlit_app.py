def create_folder_structure(service, structure, source_folder_name, destination_folder_id, is_shared_drive, dest_drive_id=None, progress_callback=None):
    """Create the folder structure in the destination folder."""
    folder_id_map = {}  # Maps source paths to destination folder IDs
    folder_id_map[""] = destination_folder_id
    
    # Sort paths by depth to ensure parent folders are created before children
    paths = sorted(structure.keys(), key=lambda x: len(x.split(os.sep)))
    
    if progress_callback:
        progress_callback(f"Processing {len(paths)} paths in order")
    
    # Process each path level
    for path in paths:
        # Get parent path and ID
        if path == "":
            parent_id = destination_folder_id
        else:
            parent_path = os.path.dirname(path) if path else ""
            
            # Debug output
            if progress_callback and debug_mode:
                progress_callback(f"For path '{path}', parent path is '{parent_path}'")
                
            # Skip if parent path isn't in our map (this shouldn't happen with proper sorting)
            if parent_path not in folder_id_map:
                progress_callback(f"Warning: Parent path '{parent_path}' not found in folder map. Available paths: {list(folder_id_map.keys())}")
                continue
                
            parent_id = folder_id_map[parent_path]
        
        # Create each folder in this path
        for folder_name in structure[path]:
            # Skip the source folder at the root level if needed
            if path == "" and folder_name == source_folder_name:
                # Map the source folder name to the destination ID
                folder_id_map[folder_name] = destination_folder_id
                continue
                
            # Create folder with support for shared drives
            folder_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [parent_id]
            }
            
            # Add parameters for shared drives if needed
            params = {'fields': 'id'}
            if is_shared_drive:
                params['supportsAllDrives'] = True
            
            try:
                folder = service.files().create(body=folder_metadata, **params).execute()
                new_folder_id = folder.get('id')
                
                # Calculate the full path for this new folder
                if path:
                    new_path = os.path.join(path, folder_name)
                else:
                    new_path = folder_name
                
                # Add to our mapping
                folder_id_map[new_path] = new_folder_id
                
                # Update progress
                if progress_callback:
                    progress_callback(f"Created: {new_path} (ID: {new_folder_id})")
            except Exception as e:
                if progress_callback:
                    progress_callback(f"Error creating folder '{folder_name}' in '{path}': {str(e)}")
    
    return folder_id_map

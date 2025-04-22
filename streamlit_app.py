import streamlit as st
import os
import json
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

st.set_page_config(page_title="Google Drive Folder Replicator", layout="wide")
st.title("Google Drive Folder Structure Replicator")
st.subheader("With Shared Drive Support")

# Authentication methods
auth_method = st.sidebar.radio(
    "Authentication Method",
    ["Service Account", "OAuth 2.0"]
)

def display_nested_structure(structure, source_folder_name=""):
    """Convert the flat path structure to a nested dictionary for better visualization."""
    nested = {}
    
    # First handle the root folder
    if "" in structure and structure[""]:
        for folder in structure[""]:
            if folder == source_folder_name:
                nested[folder] = {}
    
    # Then process all paths
    for path in sorted(structure.keys(), key=lambda x: len(x.split(os.sep))):
        if path == "":
            continue  # Already handled root
            
        parts = path.split(os.sep)
        current = nested
        
        # Navigate to the correct position in the nested structure
        for part in parts:
            if part in current:
                current = current[part]
            else:
                # This shouldn't happen with a properly built structure
                current[part] = {}
                current = current[part]
        
        # Add the folders at this path level
        for folder in structure[path]:
            current[folder] = {}
    
    return nested

def print_nested_structure(nested, indent=0):
    """Print the nested structure with indentation."""
    result = []
    for key, value in nested.items():
        result.append("  " * indent + f"└── {key}")
        children = print_nested_structure(value, indent + 1)
        result.extend(children)
    return result


def authenticate_with_service_account(credentials_content=None):
    """Authenticate using service account credentials."""
    if credentials_content:
        # Write the credentials content to a temporary file
        with open("temp_credentials.json", "w") as f:
            f.write(credentials_content)
        
        credentials = service_account.Credentials.from_service_account_file(
            "temp_credentials.json", 
            scopes=['https://www.googleapis.com/auth/drive']
        )
        
        # Remove the temporary file
        os.remove("temp_credentials.json")
        
        service = build('drive', 'v3', credentials=credentials)
        return service
    return None

def authenticate_with_oauth():
    """Authenticate using OAuth 2.0."""
    SCOPES = ['https://www.googleapis.com/auth/drive']
    creds = None
    
    # The file token.json stores the user's access and refresh tokens.
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_info(
            json.loads(open('token.json').read()),
            SCOPES
        )
    
    # If there are no valid credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists('credentials.json'):
                st.error("Please upload the OAuth credentials file first.")
                return None
                
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=8501)
            
        # Save the credentials for the next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    
    service = build('drive', 'v3', credentials=creds)
    return service

def get_folder_structure(service, folder_id, is_shared_drive, drive_id=None, path="", structure=None, progress_callback=None):
    """Recursively get the folder structure starting from a folder ID."""
    if structure is None:
        structure = {}
    
    # Get folder name - with support for shared drives
    try:
        params = {'fileId': folder_id, 'fields': 'name,mimeType'}
        if is_shared_drive:
            params['supportsAllDrives'] = True
        
        folder = service.files().get(**params).execute()
        folder_name = folder.get('name')
        current_path = os.path.join(path, folder_name) if path else folder_name
        
        # Add folder to structure
        if path not in structure:
            structure[path] = []
        structure[path].append(folder_name)
        
        # List all files and folders in this folder - with support for shared drives
        query = f"'{folder_id}' in parents and trashed = false and mimeType = 'application/vnd.google-apps.folder'"
        list_params = {
            'q': query,
            'fields': "files(id, name, mimeType)",
        }
        
        if is_shared_drive:
            list_params['supportsAllDrives'] = True
            if drive_id:
                list_params['driveId'] = drive_id
                list_params['corpora'] = 'drive'
            # Handle API differences
            try:
                list_params['includeItemsFromAllDrives'] = True
            except Exception:
                pass  # We'll handle this parameter in the try/except block later
        
        # Try with current API version first
        try:
            results = service.files().list(**list_params).execute()
        except Exception as e:
            # If it fails due to includeItemsFromAllDrives, try the older parameter
            if 'includeItemsFromAllDrives' in str(e):
                if 'includeItemsFromAllDrives' in list_params:
                    del list_params['includeItemsFromAllDrives']
                list_params['includeTeamDriveItems'] = True
                results = service.files().list(**list_params).execute()
            else:
                raise e
                
        items = results.get('files', [])
        
        # Update progress
        if progress_callback:
            progress_callback(f"Reading: {current_path}")
        
        # Process each item
        for item in items:
            if item['mimeType'] == 'application/vnd.google-apps.folder':
                # It's a folder, recursively process it
                get_folder_structure(service, item['id'], is_shared_drive, drive_id, current_path, structure, progress_callback)
    
    except Exception as e:
        if progress_callback:
            progress_callback(f"Error accessing {path}/{folder_id}: {str(e)}")
    
    return structure
    
def create_folder_structure(service, structure, source_folder_name, destination_folder_id, is_shared_drive, dest_drive_id=None, progress_callback=None):
    """Create the folder structure in the destination folder."""
    folder_id_map = {}  # Maps source paths to destination folder IDs
    folder_id_map[""] = destination_folder_id
    
    # Explicitly handle the root folder
    if "" in structure and source_folder_name in structure[""]:
        folder_id_map[source_folder_name] = destination_folder_id
    
    # Debug output
    if progress_callback:
        progress_callback(f"Found {len(structure)} path entries in structure")
        for path in structure:
            progress_callback(f"Path: '{path}' contains folders: {structure[path]}")
    
    # Sort paths by depth to ensure parent folders are created before children
    paths = sorted(structure.keys(), key=lambda x: x.count(os.sep))
    if progress_callback:
        progress_callback(f"Processing paths in order: {paths}")
    
    # Process each path level
    for path in paths:
        # Skip the root path as we already handled it
        if path == "" and source_folder_name in structure[path]:
            continue
            
        # Get parent path and ID
        parent_path = os.path.dirname(path) if path else ""
        
        # Debug output
        if progress_callback:
            progress_callback(f"For path '{path}', parent path is '{parent_path}'")
            
        # Skip if parent path isn't in our map (this shouldn't happen with proper sorting)
        if parent_path not in folder_id_map and path:
            progress_callback(f"Warning: Parent path '{parent_path}' not found in folder map. Available paths: {list(folder_id_map.keys())}")
            continue
            
        parent_id = folder_id_map.get(parent_path, destination_folder_id)
        
        # Create each folder in this path
        for folder_name in structure[path]:
            # Skip the source folder at the root level
            if path == "" and folder_name == source_folder_name:
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
                new_path = os.path.join(path, folder_name) if path else folder_name
                
                # Add to our mapping
                folder_id_map[new_path] = new_folder_id
                
                # Update progress
                if progress_callback:
                    progress_callback(f"Created: {new_path} (ID: {new_folder_id})")
            except Exception as e:
                if progress_callback:
                    progress_callback(f"Error creating folder '{folder_name}' in '{path}': {str(e)}")
    
    return folder_id_map

def validate_folder_id(service, folder_id, is_shared_drive=False):
    """Validate that a folder ID exists and is accessible."""
    try:
        params = {'fileId': folder_id, 'fields': "name, mimeType, driveId"}
        if is_shared_drive:
            params['supportsAllDrives'] = True
        
        folder = service.files().get(**params).execute()
        
        if folder['mimeType'] != 'application/vnd.google-apps.folder':
            return False, "The provided ID is not a folder", None
        
        drive_id = folder.get('driveId', None)
        return True, folder['name'], drive_id
    except Exception as e:
        return False, f"Error accessing folder: {str(e)}", None

def get_shared_drives(service):
    """Get a list of shared drives the user has access to."""
    try:
        shared_drives = []
        page_token = None
        
        # Try newer API first
        try:
            while True:
                response = service.drives().list(pageSize=100, pageToken=page_token).execute()
                shared_drives.extend(response.get('drives', []))
                page_token = response.get('nextPageToken')
                if not page_token:
                    break
        except:
            # If newer API fails, try with teamdrives() instead
            while True:
                response = service.teamdrives().list(pageSize=100, pageToken=page_token).execute()
                shared_drives.extend(response.get('teamDrives', []))
                page_token = response.get('nextPageToken')
                if not page_token:
                    break
                
        return shared_drives
    except Exception as e:
        st.sidebar.error(f"Error retrieving shared drives: {str(e)}")
        return []

# Add Debug Mode
debug_mode = st.sidebar.checkbox("Debug Mode", value=False)

# Sidebar for authentication
with st.sidebar:
    st.header("Authentication")
    
    if auth_method == "Service Account":
        st.info("Upload your service account credentials JSON file")
        credentials_file = st.file_uploader("Service Account Credentials", type=["json"])
        
        if credentials_file is not None:
            credentials_content = credentials_file.getvalue().decode("utf-8")
            try:
                # Test service account authentication
                with st.spinner("Authenticating..."):
                    drive_service = authenticate_with_service_account(credentials_content)
                    if drive_service:
                        st.success("Authentication successful!")
                        st.session_state.drive_service = drive_service
                    else:
                        st.error("Authentication failed. Please check your credentials.")
            except Exception as e:
                st.error(f"Authentication error: {str(e)}")
    
    else:  # OAuth 2.0
        st.info("Upload your OAuth 2.0 client credentials")
        oauth_credentials = st.file_uploader("OAuth 2.0 Credentials", type=["json"])
        
        if oauth_credentials is not None:
            # Save the uploaded credentials file
            with open("credentials.json", "wb") as f:
                f.write(oauth_credentials.getbuffer())
            
            if st.button("Authenticate"):
                with st.spinner("Opening authentication window..."):
                    try:
                        drive_service = authenticate_with_oauth()
                        if drive_service:
                            st.success("Authentication successful!")
                            st.session_state.drive_service = drive_service
                        else:
                            st.error("Authentication failed.")
                    except Exception as e:
                        st.error(f"Authentication error: {str(e)}")

    # Shared Drive options
    st.header("Drive Options")
    use_shared_drive = st.checkbox("Working with Shared Drives", value=True)
    
    if use_shared_drive and 'drive_service' in st.session_state:
        st.subheader("Available Shared Drives")
        shared_drives = get_shared_drives(st.session_state.drive_service)
        
        if shared_drives:
            drive_ids = []
            for drive in shared_drives:
                # Handle different API responses
                drive_id = drive.get('id') or drive.get('driveId') or drive.get('teamDriveId')
                drive_name = drive.get('name')
                if drive_id and drive_name:
                    st.write(f"- {drive_name} ({drive_id})")
                    drive_ids.append(drive_id)
            
            if debug_mode and drive_ids:
                st.text_area("Shared Drive IDs (for debug)", value="\n".join(drive_ids))
        else:
            st.info("No shared drives found or insufficient permissions.")

# Main content
st.header("Folder Structure Replication")

col1, col2 = st.columns(2)
source_drive_id = None
dest_drive_id = None

with col1:
    source_folder_id = st.text_input("Source Folder ID", help="ID of the folder structure you want to copy")
    if source_folder_id and 'drive_service' in st.session_state:
        valid, name, drive_id = validate_folder_id(
            st.session_state.drive_service, 
            source_folder_id,
            is_shared_drive=use_shared_drive
        )
        if valid:
            st.success(f"Source folder: {name}")
            if drive_id:
                source_drive_id = drive_id
                st.info(f"Part of Shared Drive with ID: {drive_id}")
                if debug_mode:
                    st.text_input("Source Drive ID (for debug)", value=drive_id)
        else:
            st.error(name)

with col2:
    destination_folder_id = st.text_input("Destination Folder ID", help="ID of the folder where structure will be created")
    if destination_folder_id and 'drive_service' in st.session_state:
        valid, name, drive_id = validate_folder_id(
            st.session_state.drive_service, 
            destination_folder_id,
            is_shared_drive=use_shared_drive
        )
        if valid:
            st.success(f"Destination folder: {name}")
            if drive_id:
                dest_drive_id = drive_id
                st.info(f"Part of Shared Drive with ID: {drive_id}")
                if debug_mode:
                    st.text_input("Destination Drive ID (for debug)", value=drive_id)
        else:
            st.error(name)

# Manual drive ID entry for problematic cases
if debug_mode:
    st.subheader("Manual Drive ID Override (Debug)")
    manual_source_drive_id = st.text_input("Manual Source Drive ID")
    if manual_source_drive_id:
        source_drive_id = manual_source_drive_id
    
    manual_dest_drive_id = st.text_input("Manual Destination Drive ID")
    if manual_dest_drive_id:
        dest_drive_id = manual_dest_drive_id

if st.button("Replicate Folder Structure") and 'drive_service' in st.session_state:
    if not source_folder_id or not destination_folder_id:
        st.error("Please provide both source and destination folder IDs")
    else:
        try:
            # Create a progress indicator
            progress_placeholder = st.empty()
            status_text = st.empty()
            
            # Get source folder name
            params = {'fileId': source_folder_id, 'fields': "name"}
            if use_shared_drive:
                params['supportsAllDrives'] = True
            
            source_folder = st.session_state.drive_service.files().get(**params).execute()
            source_folder_name = source_folder.get('name')
            
            # Get folder structure
            status_text.text("Reading folder structure...")
            structure = get_folder_structure(
                st.session_state.drive_service, 
                source_folder_id,
                is_shared_drive=use_shared_drive,
                drive_id=source_drive_id,
                progress_callback=lambda msg: status_text.text(msg)
            )
            
            # Display the structure
            
           with st.expander("Folder Structure (Tree View)"):
               if structure:    
                nested_structure = display_nested_structure(structure, source_folder_name)
                tree_lines = print_nested_structure(nested_structure)
        
            # Display the tree structure
                for line in tree_lines:
                    st.text(line)
            
            # Also show raw structure for debugging
            with st.expander("Raw Structure Data"):
                st.json(structure)
            
            # Create structure in destination
            status_text.text("Replicating folder structure...")
            create_folder_structure(
                st.session_state.drive_service, 
                structure, 
                source_folder_name, 
                destination_folder_id,
                is_shared_drive=use_shared_drive,
                dest_drive_id=dest_drive_id,
                progress_callback=lambda msg: status_text.text(msg)
            )
            
            status_text.text("Folder structure replication completed!")
            st.success("Folder structure has been successfully replicated!")
            
        except Exception as e:
            st.error(f"Error: {str(e)}")
            if debug_mode:
                import traceback
                st.code(traceback.format_exc())

# Instructions
with st.expander("Instructions & Troubleshooting"):
    st.markdown("""
    ### Working with Shared Drives (Team Drives)
    
    1. **Enable Shared Drive support**:
       - Check the "Working with Shared Drives" box in the sidebar
    
    2. **Find Folder IDs in Shared Drives**:
       - Open the folder in your browser
       - The ID is in the URL after "folders/" (example: https://drive.google.com/drive/u/0/folders/1ABCdefGHIjkLMnop)
       - For Shared Drive root folders, the ID is after "drives/" in the URL
       
    3. **Permissions**:
       - Your service account must be added to the shared drive with appropriate permissions
       - Or your account must have access if using OAuth
       
    4. **Available Shared Drives**:
       - After authenticating, your accessible shared drives will be listed in the sidebar
       
    ### Common Issues & Solutions
    
    - **"includeItemsFromAllDrives" error**:
      - Enable Debug Mode and try running again
      - The app will attempt to use compatible parameters for your API version
      
    - **404 Errors**: 
      - Make sure the "Working with Shared Drives" checkbox is enabled
      - Verify you have proper permissions
      - Confirm the folder ID is correct
      - Try enabling Debug Mode and manually enter the Drive ID
      
    - **Permission Issues**:
      - For service accounts, add the service account email to the shared drive
      - For OAuth, ensure your account has access to both source and destination
      
    - **API Compatibility Issues**:
      - If you encounter strange API errors, enable Debug Mode
      - Use the manual Drive ID override if automatic detection fails
    """)

st.sidebar.markdown("---")
st.sidebar.info("This app requires proper permissions to access and modify Google Drive folders.")

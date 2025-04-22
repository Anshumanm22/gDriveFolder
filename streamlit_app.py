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

# Authentication methods
auth_method = st.sidebar.radio(
    "Authentication Method",
    ["Service Account", "OAuth 2.0"]
)

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

def get_folder_structure(service, folder_id, path="", structure=None, progress_callback=None):
    """Recursively get the folder structure starting from a folder ID."""
    if structure is None:
        structure = {}
    
    # Get folder name
    folder = service.files().get(fileId=folder_id, fields="name").execute()
    folder_name = folder.get('name')
    current_path = os.path.join(path, folder_name) if path else folder_name
    
    # Add folder to structure
    if path not in structure:
        structure[path] = []
    structure[path].append(folder_name)
    
    # List all files and folders in this folder
    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(
        q=query,
        fields="files(id, name, mimeType)").execute()
    items = results.get('files', [])
    
    # Update progress
    if progress_callback:
        progress_callback(f"Reading: {current_path}")
    
    # Process each item
    for item in items:
        if item['mimeType'] == 'application/vnd.google-apps.folder':
            # It's a folder, recursively process it
            get_folder_structure(service, item['id'], current_path, structure, progress_callback)
    
    return structure

def create_folder_structure(service, structure, source_folder_name, destination_folder_id, progress_callback=None):
    """Create the folder structure in the destination folder."""
    folder_id_map = {}  # Maps source paths to destination folder IDs
    folder_id_map[""] = destination_folder_id
    
    # Process the structure level by level
    for path in sorted(structure.keys(), key=lambda x: len(x.split(os.sep))):
        parent_path = os.path.dirname(path)
        parent_id = folder_id_map.get(parent_path, destination_folder_id)
        
        for folder_name in structure[path]:
            # Skip the source folder itself
            if path == "" and folder_name == source_folder_name:
                folder_id_map[folder_name] = destination_folder_id
                continue
                
            # Create folder
            folder_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [parent_id]
            }
            
            folder = service.files().create(body=folder_metadata, fields='id').execute()
            new_folder_id = folder.get('id')
            
            # Add to our mapping
            new_path = os.path.join(path, folder_name) if path else folder_name
            folder_id_map[new_path] = new_folder_id
            
            # Update progress
            if progress_callback:
                progress_callback(f"Created: {new_path}")
    
    return folder_id_map

def validate_folder_id(service, folder_id):
    """Validate that a folder ID exists and is accessible."""
    try:
        folder = service.files().get(fileId=folder_id, fields="name, mimeType").execute()
        if folder['mimeType'] != 'application/vnd.google-apps.folder':
            return False, "The provided ID is not a folder"
        return True, folder['name']
    except Exception as e:
        return False, f"Error accessing folder: {str(e)}"

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

# Main content
st.header("Folder Structure Replication")

col1, col2 = st.columns(2)

with col1:
    source_folder_id = st.text_input("Source Folder ID", help="ID of the folder structure you want to copy")
    if source_folder_id and 'drive_service' in st.session_state:
        valid, name = validate_folder_id(st.session_state.drive_service, source_folder_id)
        if valid:
            st.success(f"Source folder: {name}")
        else:
            st.error(name)

with col2:
    destination_folder_id = st.text_input("Destination Folder ID", help="ID of the folder where structure will be created")
    if destination_folder_id and 'drive_service' in st.session_state:
        valid, name = validate_folder_id(st.session_state.drive_service, destination_folder_id)
        if valid:
            st.success(f"Destination folder: {name}")
        else:
            st.error(name)

if st.button("Replicate Folder Structure") and 'drive_service' in st.session_state:
    if not source_folder_id or not destination_folder_id:
        st.error("Please provide both source and destination folder IDs")
    else:
        try:
            # Create a progress indicator
            progress_placeholder = st.empty()
            status_text = st.empty()
            
            # Get source folder name
            source_folder = st.session_state.drive_service.files().get(fileId=source_folder_id, fields="name").execute()
            source_folder_name = source_folder.get('name')
            
            # Get folder structure
            status_text.text("Reading folder structure...")
            structure = get_folder_structure(
                st.session_state.drive_service, 
                source_folder_id,
                progress_callback=lambda msg: status_text.text(msg)
            )
            
            # Display the structure
            with st.expander("Folder Structure"):
                for path, folders in structure.items():
                    for folder in folders:
                        full_path = os.path.join(path, folder) if path else folder
                        st.text(full_path)
            
            # Create structure in destination
            status_text.text("Replicating folder structure...")
            create_folder_structure(
                st.session_state.drive_service, 
                structure, 
                source_folder_name, 
                destination_folder_id,
                progress_callback=lambda msg: status_text.text(msg)
            )
            
            status_text.text("Folder structure replication completed!")
            st.success("Folder structure has been successfully replicated!")
            
        except Exception as e:
            st.error(f"Error: {str(e)}")

# Instructions
with st.expander("Instructions"):
    st.markdown("""
    ### How to use this app
    
    1. **Authentication**:
       - Choose your authentication method in the sidebar
       - Upload the required credentials file
       
    2. **Get Folder IDs**:
       - In Google Drive, navigate to the folder
       - From the URL, copy the ID (the long string after "folders/" in the URL)
       
    3. **Replicate Structure**:
       - Enter the source folder ID (the folder structure you want to copy)
       - Enter the destination folder ID (where you want to create the structure)
       - Click "Replicate Folder Structure"
       
    4. **Monitor Progress**:
       - The app will show the progress as it reads and creates folders
       - Once complete, you can check your Google Drive to see the replicated structure
       
    ### Notes
    
    - This app only replicates the folder structure, not the files
    - Make sure your service account or user has proper permissions on both folders
    """)

st.sidebar.markdown("---")
st.sidebar.info("This app requires proper permissions to access and modify Google Drive folders.")

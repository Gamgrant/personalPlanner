"""
ADK Greeting Chat Application - Main Entry Point
"""
from ui.streamlit_ui import run_streamlit_app # Import the function that contains our Streamlit app logic.
if __name__ == "__main__":
    print("🚀 Starting ADK Greeting Chat Application...") # Console message for debugging.
    run_streamlit_app() # Call the function to launch the Streamlit application.
    print("✅ Application started successfully!") # Console message upon successful launch.
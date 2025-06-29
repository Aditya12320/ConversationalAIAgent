
import streamlit as st
import requests
import os
from dotenv import load_dotenv
import webbrowser
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

st.set_page_config(page_title="Calendar Booking Assistant", page_icon="📅")
st.title("📅 Calendar Booking Assistant")

if "messages" not in st.session_state:
    st.session_state.messages = []

if "user_id" not in st.session_state:
    st.session_state.user_id = "user_123"  # In production, use actual user ID

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

backend_url = os.getenv("BACKEND_URL", "http://localhost:8000")

def check_auth():
    try:
        response = requests.get(
            f"{backend_url}/api/auth/status",
            params={"user_id": st.session_state.user_id},
            timeout=5
        )
        if response.status_code == 200:
            auth_status = response.json()
            st.session_state.authenticated = auth_status.get("authenticated", False)
            if not st.session_state.authenticated and auth_status.get("error"):
                st.error(f"Auth error: {auth_status['error']}")
            return st.session_state.authenticated
        return False
    except Exception as e:
        st.error(f"Connection error: {str(e)}")
        return False


def authenticate():
    auth_url = f"{backend_url}/api/auth?user_id={st.session_state.user_id}"  # Removed duplicate /auth
    webbrowser.open_new_tab(auth_url)

if st.session_state.authenticated and st.sidebar.button("Logout"):
    st.session_state.authenticated = False
    st.session_state.messages = []
    st.experimental_rerun()

if not check_auth():
    st.warning("Please authenticate with Google Calendar to continue.")
    if st.button("Login with Google"):
        authenticate()
    st.stop()

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("How can I help you book an appointment?"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    try:
        response = requests.post(
            f"{backend_url}/chat",
            json={
                "message": prompt,
                "user_id": st.session_state.user_id
            },
            timeout=30
        )

        if response.status_code == 401:
            st.error("Please authenticate first")
            authenticate()
            st.experimental_rerun()

        response.raise_for_status()
        assistant_response = response.json().get("response", "Sorry, I didn't understand that.")

    except Exception as e:
        assistant_response = f"Error: Unable to connect to backend. {str(e)}"

    st.session_state.messages.append({"role": "assistant", "content": assistant_response})
    with st.chat_message("assistant"):
        st.markdown(assistant_response)
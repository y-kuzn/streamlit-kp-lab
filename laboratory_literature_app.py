import streamlit as st
import debug_utils
st.set_page_config(page_title="📚 AI Literature Helper", page_icon="🤖")
import requests, json, re, os, io
import xml.etree.ElementTree as ET
from pyzotero import zotero
import fitz  # PyMuPDF
from time import sleep, time
from requests import RequestException
from datetime import datetime, timedelta
import hashlib

# ----------------------------
# OPENAI (ChatGPT)
# ----------------------------
from openai import OpenAI
import requests

# ============================
# CONFIG
# ============================


SEMANTIC_SCHOLAR_API_KEY = st.secrets[SEMANTIC_SCHOLAR_API_KEY]

# OpenAI API Key
OPENAI_API_KEY = st.secrets[OPENAI_API_KEY]
NCBI_EMAIL = st.secrets[NCBI_EMAIL]
NCBI_API_KEY = st.secrets[NCBI_API_KEY]

# Initialize OpenAI client with error handling
openai_client = None
OPENAI_ENABLED = False

if OPENAI_API_KEY and OPENAI_API_KEY != "REPLACE_WITH_YOUR_OPENAI_API_KEY":
    try:
        # Only pass api_key, do NOT pass proxies or other unsupported args
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
        # Quick test of the API key
        test_response = requests.get(
            'https://api.openai.com/v1/models',
            headers={'Authorization': f'Bearer {OPENAI_API_KEY}'},
            timeout=5
        )
        if test_response.status_code == 200:
            OPENAI_ENABLED = True
        else:
            st.error(f"⚠️ OpenAI API key is invalid (Status: {test_response.status_code})")
            st.info("📝 Please update OPENAI_API_KEY with a valid key from https://platform.openai.com/account/api-keys")
            openai_client = None
    except Exception as e:
        st.error(f"⚠️ OpenAI initialization failed: {e}")
        openai_client = None
else:
    st.info("📝 Please set OPENAI_API_KEY to enable AI features")
    st.info("   Get your API key from: https://platform.openai.com/account/api-keys")

SLEEP = 0.08  # pacing for retries/backoff
USERS_FILE = "users.json"
ADMIN_KEYWORD = "AmyloNMRCryo42!"

# ============================
# USER ACCOUNT SYSTEM
# ============================
def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        return json.load(open(USERS_FILE))
    except Exception:
        return {}

def save_users(users_data):
    with open(USERS_FILE, "w") as f:
        json.dump(users_data, f, indent=2)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def create_user(username, password, email="", admin_keyword=None):
    users = load_users()
    if username in users:
        return False, "Username already exists"
    is_admin = (admin_keyword == ADMIN_KEYWORD)
    users[username] = {
        "password_hash": hash_password(password),
        "email": email,
        "created_at": datetime.now().isoformat(),
        "is_admin": is_admin,
        "profile": {
            "topics": [],
            "authors": [],
            "journals": [],
            "zotero_key": "",
            "zotero_id": "",
            "zotero_collection": "",
            "search_preferences": {
                "default_source": "Semantic Scholar",
                "max_results": 20,
                "min_score": 2
            }
        }
    }
    save_users(users)
    return True, "User created successfully"

def authenticate_user(username, password):
    users = load_users()
    if username not in users:
        return False, "User not found"
    
    if users[username]["password_hash"] == hash_password(password):
        return True, "Authentication successful"
    return False, "Invalid password"

def get_user_profile(username):
    users = load_users()
    return users.get(username, {}).get("profile", {})

def update_user_profile(username, profile_data):
    users = load_users()
    if username in users:
        users[username]["profile"].update(profile_data)
        save_users(users)
        return True
    return False

# ============================
# SESSION STATE MANAGEMENT
# ============================
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = ""
if "user_profile" not in st.session_state:
    st.session_state.user_profile = {}

# ============================
# USER INTERFACE FOR ACCOUNTS
# ============================
def render_login_signup():
    st.title("📚 AI Literature Helper")
    
    tab1, tab2 = st.tabs(["🔑 Login", "📝 Sign Up"])

    # --- LOGIN TAB ---
    with tab1:
        st.subheader("Login to your account")
        username = st.text_input("Username", key="login_username")
        password = st.text_input("Password", type="password", key="login_password")

        col1, col2 = st.columns([2, 1])
        with col1:
            login_clicked = st.button("🔑 Login")
        with col2:
            forgot_clicked = st.button("Forgot password?")

        if login_clicked:
            if username and password:
                success, message = authenticate_user(username, password)
                if success:
                    st.session_state.logged_in = True
                    st.session_state.username = username
                    st.session_state.user_profile = get_user_profile(username)
                    st.success(message)
                    st.rerun()
                else:
                    st.error(message)
            else:
                st.warning("Please enter both username and password")

        # Password reset workflow
        if forgot_clicked or st.session_state.get("show_reset", False):
            st.session_state.show_reset = True
            st.info("Enter your username and email to reset your password.")
            reset_username = st.text_input("Username for reset", key="reset_username")
            reset_email = st.text_input("Email for reset", key="reset_email")
            if st.button("Send reset code"):
                users = load_users()
                user = users.get(reset_username)
                if user and user.get("email") and user["email"].lower() == reset_email.lower():
                    import random
                    reset_code = str(random.randint(100000, 999999))
                    st.session_state.reset_code = reset_code
                    st.session_state.reset_user = reset_username
                    st.success(f"Reset code: {reset_code} (for demo, shown here)")
                else:
                    st.error("Username and email do not match any account.")
            if st.session_state.get("reset_code"):
                code_entered = st.text_input("Enter reset code", key="reset_code_input")
                new_pw = st.text_input("New password", type="password", key="reset_new_pw")
                confirm_pw = st.text_input("Confirm new password", type="password", key="reset_confirm_pw")
                if st.button("Reset password"):
                    if code_entered == st.session_state.reset_code:
                        if new_pw == confirm_pw and new_pw:
                            users = load_users()
                            users[st.session_state.reset_user]["password_hash"] = hash_password(new_pw)
                            save_users(users)
                            st.success("Password reset! Please login.")
                            st.session_state.show_reset = False
                            st.session_state.reset_code = None
                            st.session_state.reset_user = None
                        else:
                            st.error("Passwords do not match or are empty.")
                    else:
                        st.error("Incorrect reset code.")

    # --- SIGNUP TAB ---
    with tab2:
        st.subheader("Create a new account")
        new_username = st.text_input("Choose Username", key="signup_username")
        new_email = st.text_input("Email (optional)", key="signup_email")
        new_password = st.text_input("Choose Password", type="password", key="signup_password")
        confirm_password = st.text_input("Confirm Password", type="password", key="confirm_password")
        lab_passcode = st.text_input("Lab Passcode (ask your PI)", key="signup_passcode", help="Hint: It's something fun about amyloids!")
        admin_keyword = st.text_input("Admin Keyword (optional, for admins)", key="signup_admin_keyword", type="password", help="Only for admin users")

        AMYLOID_PASSCODE = "amyloidfibril"

        if st.button("📝 Create Account"):
            if not lab_passcode or lab_passcode.strip().lower() != AMYLOID_PASSCODE:
                st.error("Lab passcode incorrect! Only lab members can register.")
            elif new_username and new_password and confirm_password:
                if new_password == confirm_password:
                    success, message = create_user(new_username, new_password, new_email, admin_keyword)
                    if success:
                        st.success(message)
                        st.info("Please login with your new credentials")
                    else:
                        st.error(message)
                else:
                    st.error("Passwords do not match")
            else:
                st.warning("Please fill in all required fields")

def is_admin():
    users = load_users()
    return users.get(st.session_state.username, {}).get("is_admin", False)

def render_admin_panel():
    st.sidebar.markdown("---")
    st.sidebar.markdown("**🛡️ Admin Panel**")
    users = load_users()
    # Set group Zotero API credentials
    st.sidebar.markdown("**Group Zotero API Settings**")
    group_zotero_key = st.sidebar.text_input("Group Zotero API Key", value=st.session_state.get("group_zotero_key", ""), type="password")
    group_zotero_id = st.sidebar.text_input("Group Zotero User ID", value=st.session_state.get("group_zotero_id", ""))
    group_zotero_collection = st.sidebar.text_input("Group Zotero Collection ID", value=st.session_state.get("group_zotero_collection", ""))
    if st.sidebar.button("Save Group Zotero Settings"):
        st.session_state.group_zotero_key = group_zotero_key
        st.session_state.group_zotero_id = group_zotero_id
        st.session_state.group_zotero_collection = group_zotero_collection
        st.sidebar.success("Group Zotero settings saved for this session.")
    # Set default interests
    st.sidebar.markdown("**Set Default Interests for All Users**")
    default_topics = st.sidebar.text_input("Default Topics (comma-separated)", value=", ".join(st.session_state.get("default_topics", [])))
    default_authors = st.sidebar.text_input("Default Authors (comma-separated)", value=", ".join(st.session_state.get("default_authors", [])))
    default_journals = st.sidebar.text_input("Default Journals (comma-separated)", value=", ".join(st.session_state.get("default_journals", [])))
    if st.sidebar.button("Apply Defaults to All Users"):
        topics = [t.strip() for t in default_topics.split(",") if t.strip()]
        authors = [a.strip() for a in default_authors.split(",") if a.strip()]
        journals = [j.strip() for j in default_journals.split(",") if j.strip()]
        for uname, udata in users.items():
            udata["profile"]["topics"] = topics
            udata["profile"]["authors"] = authors
            udata["profile"]["journals"] = journals
        save_users(users)
        st.session_state.default_topics = topics
        st.session_state.default_authors = authors
        st.session_state.default_journals = journals
        st.sidebar.success("Defaults applied to all users.")
    # Delete users
    st.sidebar.markdown("**Delete User**")
    del_user = st.sidebar.selectbox("Select user to delete", [u for u in users if u != st.session_state.username])
    if st.sidebar.button("Delete Selected User"):
        if del_user in users:
            del users[del_user]
            save_users(users)
            st.sidebar.success(f"User '{del_user}' deleted.")

def render_user_profile():
    st.sidebar.markdown("---")
    st.sidebar.subheader(f"👤 Welcome, {st.session_state.username}!")

    if is_admin():
        render_admin_panel()
        # ...existing admin panel code...

    if st.sidebar.button("🔓 Logout"):
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.session_state.user_profile = {}
        st.rerun()

    with st.sidebar.expander("⚙️ Profile Settings", expanded=False):
        profile = st.session_state.user_profile
        
        # Tracked topics and authors
        topics_txt = st.text_input("Priority Topics (comma-separated)", 
                                 ", ".join(profile.get("topics", [])))
        authors_txt = st.text_input("Priority Authors (comma-separated)", 
                                  ", ".join(profile.get("authors", [])))
        journals_txt = st.text_input("Priority Journals (comma-separated)", 
                                  ", ".join(profile.get("journals", [])))
        
        # Zotero credentials
        st.markdown("**Zotero Settings**")
        zotero_key = st.text_input("Zotero API Key", 
                                 value=profile.get("zotero_key", ""), type="password")
        zotero_id = st.text_input("Zotero User ID", 
                                value=profile.get("zotero_id", ""))
        zotero_collection = st.text_input("Default Zotero Collection ID", 
                                        value=profile.get("zotero_collection", ""))
        
        # Search preferences
        st.markdown("**Search Preferences**")
        default_source = st.selectbox("Default Search Source", 
                                    ["Semantic Scholar", "PubMed", "Both"],
                                    index=["Semantic Scholar", "PubMed", "Both"].index(
                                        profile.get("search_preferences", {}).get("default_source", "Semantic Scholar")))
        default_max_results = st.slider("Default Max Results", 5, 100, 
                                       profile.get("search_preferences", {}).get("max_results", 20))
        default_min_score = st.slider("Default Min Score", 0, 3, 
                                     profile.get("search_preferences", {}).get("min_score", 2))
        
        if st.button("💾 Save Profile"):
            updated_profile = {
                "topics": [t.strip() for t in topics_txt.split(",") if t.strip()],
                "authors": [a.strip() for a in authors_txt.split(",") if a.strip()],
                "journals": [j.strip() for j in journals_txt.split(",") if j.strip()],
                "zotero_key": zotero_key,
                "zotero_id": zotero_id,
                "zotero_collection": zotero_collection,
                "search_preferences": {
                    "default_source": default_source,
                    "max_results": default_max_results,
                    "min_score": default_min_score
                }
            }
            
            if update_user_profile(st.session_state.username, updated_profile):
                st.session_state.user_profile.update(updated_profile)
                st.success("Profile updated successfully!")
            else:
                st.error("Failed to update profile")

# ============================
# COMPATIBILITY FUNCTIONS (for existing code)
# ============================
def get_current_prefs():
    """Get current user preferences or defaults"""
    if st.session_state.logged_in:
        profile = st.session_state.user_profile
        return {
            "topics": profile.get("topics", []),
            "authors": profile.get("authors", [])
        }
    return {"topics": [], "authors": []}

prefs = get_current_prefs()

# ============================
# UI
# ============================

# Show login/signup interface if not logged in
if not st.session_state.logged_in:
    render_login_signup()
    st.stop()



SLEEP = 0.08  # pacing for retries/backoff
USERS_FILE = "users.json"
ADMIN_KEYWORD = "AmyloNMRCryo42!"

# ============================
### --- streamlit-authenticator will handle authentication and user management --- ###

# ============================
### --- Session state will be managed by streamlit-authenticator --- ###

# ============================
### --- Login/signup UI will be handled by streamlit-authenticator --- ###

### --- Admin check will be handled by streamlit-authenticator roles or config --- ###

### --- Admin panel will be re-implemented if needed using streamlit-authenticator roles --- ###

### --- User profile UI will be re-implemented after authenticator integration --- ###

# ============================
# COMPATIBILITY FUNCTIONS (for existing code)
# ============================
### --- User preferences will be handled after authenticator integration --- ###

# ============================
# UI
# ============================

### --- Authenticator login UI and main app interface will be added next --- ###

st.title("📚 AI Literature Helper")

# Get user preferences for defaults
profile = st.session_state.user_profile
search_prefs = profile.get("search_preferences", {})

search_mode = st.radio(
    "🔍 What would you like to do?",
    [
        "Keyword Search",
        "Paste citation / page text",
        "Lookup by URL / PDF ",
    ],
    horizontal=False,
)

# Source selector ONLY for Keyword Search (removed for Paste mode per request)
search_source = st.selectbox(
    "📡 Choose search source",
    ["Semantic Scholar", "PubMed", "Both"],
    index=["Semantic Scholar", "PubMed", "Both"].index(search_prefs.get("default_source", "Semantic Scholar"))
) if search_mode == "Keyword Search" else None

# Store search source in session state for query generation
if search_source:
    st.session_state.current_search_source = search_source

max_results = st.slider("📄 Max articles to fetch:", 5, 100, 
                       search_prefs.get("max_results", 20), 1)

# Unified relevance is score3 (0..3)
min_score3 = st.slider("⭐ Minimum AI relevance score3 to save to Zotero (0-3):", 0, 3, 
                      search_prefs.get("min_score", 2), 1)

# Date range filter for keyword search
date_filter_enabled = False
date_from = date_to = None

if search_mode == "Keyword Search":
    use_boolean = st.checkbox("🔤 Convert to Boolean query (AI-optimized)")
    
    # Add date filtering option
    date_filter_enabled = st.checkbox("🔍 Add date filter to search")
    if date_filter_enabled:
        col1, col2, col3 = st.columns(3)
        with col1:
            preset = st.selectbox("Quick presets:", 
                                ["Custom", "Last 1 month", "Last 3 months", "Last 6 months", "Last year", "Last 2 years", "Last 5 years"])
        if preset != "Custom":
            months_map = {"Last 1 month": 1, "Last 3 months": 3, "Last 6 months": 6, 
                         "Last year": 12, "Last 2 years": 24, "Last 5 years": 60}
            months = months_map[preset]
            date_from = datetime.now() - timedelta(days=months*30)
            date_to = datetime.now()
            st.info(f"📅 Searching from {date_from.strftime('%B %Y')} to {date_to.strftime('%B %Y')}")
        else:
            with col2:
                date_from = st.date_input("From date:", 
                                        value=datetime.now() - timedelta(days=365),
                                        min_value=datetime(1900, 1, 1).date(),
                                        max_value=datetime.now().date(),
                                        help="Select the earliest publication date to include")
            with col3:
                date_to = st.date_input("To date:", 
                                      value=datetime.now(),
                                      min_value=datetime(1900, 1, 1).date(),
                                      max_value=datetime.now().date(),
                                      help="Select the latest publication date to include")
            if date_from and date_to:
                if date_from > date_to:
                    st.error("❌ From date cannot be later than To date")
                else:
                    date_from = datetime.combine(date_from, datetime.min.time())
                    date_to = datetime.combine(date_to, datetime.min.time())
                    st.info(f"📅 Searching from {date_from.strftime('%B %d, %Y')} to {date_to.strftime('%B %d, %Y')}")

elif search_mode == "Paste citation / page text":
    # Text extraction workflow is handled below
    pass
else:
    url_or_doi = st.text_input("🔗 Paste a URL (landing page or PDF):")

# Use saved Zotero credentials from profile
add_to_zotero = st.checkbox("📥 Add articles to Zotero")
user_zotero_key = profile.get("zotero_key", "")
user_zotero_id = profile.get("zotero_id", "")
user_zotero_collection = profile.get("zotero_collection", "")
allow_duplicates = False

if add_to_zotero:
    if not (user_zotero_key and user_zotero_id and user_zotero_collection):
        st.warning("⚠️ Please configure your Zotero credentials in Profile Settings")
        st.info("You can set up your Zotero API key, User ID, and Collection ID in the sidebar profile settings.")
    else:
        st.success("✅ Using saved Zotero credentials")
        allow_duplicates = st.checkbox("⚠️ Allow Zotero duplicates", value=False)

# ============================
# SMART TAG MANAGEMENT
# ============================
def normalize_tags(tags):
    """
    Normalize tag format to ensure consistent hyphen format across all modes.
    Converts colons to hyphens for aRT, aTa, aTy, aMe prefixes.
    
    Args:
        tags: List of tag strings
        
    Returns:
        List of normalized tag strings with consistent hyphen format
    """
    if not tags:
        return []
    
    normalized_tags = []
    for tag in tags:
        if isinstance(tag, str) and tag.strip():
            # Convert colon-separated tags to hyphen-separated tags
            if ':' in tag and any(tag.startswith(prefix) for prefix in ['aRT', 'aTa', 'aTy', 'aMe']):
                normalized_tag = tag.replace(':', '-', 1)  # Replace only the first colon
                normalized_tags.append(normalized_tag)
            else:
                normalized_tags.append(tag)
    
    return normalized_tags

def get_existing_zotero_tags(zot):
    """Get all existing tags from Zotero library"""
    try:
        tags = zot.tags()
        # Handle different response formats from Zotero API
        tag_names = []
        for tag in tags:
            if isinstance(tag, dict) and 'tag' in tag:
                tag_names.append(tag['tag'])
            elif isinstance(tag, str):
                tag_names.append(tag)
        return tag_names
    except Exception as e:
        st.warning(f"Could not fetch existing tags: {e}")
        return []

def find_similar_tags(new_tag, existing_tags, threshold=0.7):
    """Find similar tags using simple string similarity"""
    import difflib
    similar = []
    for existing in existing_tags:
        similarity = difflib.SequenceMatcher(None, new_tag.lower(), existing.lower()).ratio()
        if similarity >= threshold:
            similar.append((existing, similarity))
    return sorted(similar, key=lambda x: x[1], reverse=True)

def smart_tag_processing(proposed_tags, zot=None):
    """Process tags to reduce redundancy and suggest mergers"""
    if not zot:
        return proposed_tags, []  # Always return tuple for consistency
    
    existing_tags = get_existing_zotero_tags(zot)
    if not existing_tags:
        return proposed_tags, []  # Always return tuple for consistency
    
    processed_tags = []
    suggestions = []
    
    for tag in proposed_tags:
        # Skip tags that already exist (exact match)
        if tag in existing_tags:
            processed_tags.append(tag)
            continue
            
        # Find similar tags
        similar = find_similar_tags(tag, existing_tags, threshold=0.7)
        
        if similar:
            # Suggest using the most similar existing tag
            best_match, similarity = similar[0]
            if similarity > 0.85:  # Very similar - auto-replace
                processed_tags.append(best_match)
                suggestions.append(f"Replaced '{tag}' with existing '{best_match}' (similarity: {similarity:.2f})")
            else:  # Similar but not identical - suggest
                processed_tags.append(tag)  # Keep original for now
                suggestions.append(f"Consider using existing '{best_match}' instead of '{tag}' (similarity: {similarity:.2f})")
        else:
            processed_tags.append(tag)
    
    return processed_tags, suggestions

# ============================
# Enhanced Gemini Boolean Query with Date Range
# ============================
# ============================
# Professor's Enhanced Functions Integration
# ============================

def what_is_requested(text_list):
    """
    gpt-5-mini classification of research query type
    Returns: (classification_integers, classification_texts)
    """
    try:
        if not text_list or not text_list[0].strip():
            return [1], ["general research"]
            
        text = text_list[0]
        
        prompt = f"""
        Classify the following research request into one of these categories:
        
        1 = General research topic (broad scientific inquiry)
        2 = Evolutionary biology research (evolution, phylogenetics, natural selection)  
        3 = Physical chemistry/biochemistry research (molecular mechanisms, thermodynamics, structural biology)
        4 = PMID list (contains 8+ digit numbers that look like PubMed IDs)
        
        Text to classify: "{text}"
        
        Respond with only the number (1, 2, 3, or 4) and a brief description.
        Format: "Number: Description"
        """
        
        if not openai_client:
            # Fallback if no OpenAI client available
            return [1], ["general research"]
            
        response = openai_client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "user", "content": prompt}
            ],
            max_tokens=200,
            temperature=0.3
        )
        
        result_text = response.choices[0].message.content.strip()
        
        # Parse response
        if ":" in result_text:
            number_part = result_text.split(":")[0].strip()
            desc_part = result_text.split(":", 1)[1].strip()
        else:
            # Fallback parsing
            number_match = re.search(r'\b([1-4])\b', result_text)
            number_part = number_match.group(1) if number_match else "1"
            desc_part = "general research"
        
        try:
            classification_int = int(number_part)
        except:
            classification_int = 1
            
        # Ensure valid range
        if classification_int not in [1, 2, 3, 4]:
            classification_int = 1
            
        # Map to description
        desc_map = {
            1: "general research",
            2: "evolutionary biology research", 
            3: "physical chemistry/biochemistry research",
            4: "PMID list"
        }
        
        final_desc = desc_map.get(classification_int, desc_part)
        
        return [classification_int], [final_desc]
        
    except Exception as e:
        st.warning(f"Classification error: {e}")
        return [1], ["general research"]

def construct_pubmed_query(text, classified_as_int):
    """
    Construct optimized PubMed query based on classification
    """
    try:
        if classified_as_int == 4:  # PMIDs
            pmid_pattern = r'\b\d{8,}\b'
            pmids = re.findall(pmid_pattern, text)
            return " OR ".join([f"{pmid}[PMID]" for pmid in pmids])
            
        # For other classifications, use GPT to construct query
        area_context = {
            1: "general research topics",
            2: "evolutionary biology research",
            3: "physical chemistry and biochemistry research"
        }
        
        context = area_context.get(classified_as_int, "general research")
        
        prompt = f"""
        Create an optimized PubMed search query for: "{text}"
        
        Context: This is for {context}
        
        Use PubMed syntax with:
        - [MeSH] terms where appropriate
        - [Title/Abstract] for key terms
        - AND/OR logic
        - Year restrictions if mentioned
        - Journal quality filters
        
        Return only the PubMed query string, no explanation.
        """
        
        if not openai_client:
            # Fallback if no OpenAI client available
            return text.replace(" ", " AND ")
            
        response = openai_client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        
        return response.choices[0].message.content.strip()
        
    except Exception as e:
        st.warning(f"Query construction error: {e}")
        # Fallback to simple query
        return text.replace(" ", " AND ")

def search_pubmed_fallback(query, limit=20):
    """
    Fallback PubMed search using web scraping when API returns 0 results
    """
    try:
        # First try regular API
        api_results = search_pubmed(query, limit)
        if api_results:
            return [r.get("url", "").split("/")[-2] for r in api_results if r.get("url")]
            
        # If API returns empty, try web scraping
        st.warning("🔄 No results from API, trying alternative search method...")
        
        search_url = f"https://pubmed.ncbi.nlm.nih.gov/?term={requests.utils.quote(query)}&size={limit}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(search_url, headers=headers, timeout=30)
        response.raise_for_status()
        
        # Extract PMIDs from HTML
        pmid_pattern = r'/(\d{8,})/'
        pmids = re.findall(pmid_pattern, response.text)
        pmids = list(set(pmids))[:limit]  # Remove duplicates and limit
        
        if pmids:
            st.success(f"✅ Found {len(pmids)} PMIDs via alternative search")
            return pmids
        else:
            st.warning("❌ No results found")
            return []
            
    except Exception as e:
        st.error(f"Fallback search error: {e}")
        return []

def remotexs_links(doi):
    """
    Generate institutional access links for DOI
    Returns list of (description, url) tuples with clear labels
    """
    if not doi:
        return []
        
    links = []
    
    # Clean DOI
    doi_clean = doi.replace("https://doi.org/", "").replace("http://dx.doi.org/", "")
    doi_url = f"https://doi.org/{doi_clean}"
    
    # Method 1: RemoteXs style 1 (user/login with dest parameter)
    remotexs_style1 = f"https://remotexs.ntu.edu.sg/user/login?dest={doi_url}"
    links.append(("🏫 NTU RemoteXs (User Portal)", remotexs_style1))
    
    # Method 2: RemoteXs style 2 (direct login with url parameter) 
    remotexs_style2 = f"https://remotexs.ntu.edu.sg/login?url={doi_url}"
    links.append(("🔗 NTU RemoteXs (Direct Login)", remotexs_style2))
    
    # Method 3: Direct DOI link as fallback
    links.append(("🌐 Direct DOI Link", doi_url))
    
    return links
    
    # Direct DOI link as backup
    links.append(f"https://doi.org/{doi_clean}")
    
    return links

def rate_publication(metadata, classification_switch):
    """
    Rate publication using gpt-5-mini based on research area and user preferences
    Returns formatted rating string with score, keywords, and notes
    """
    try:
        title = metadata.get("Title", "")
        authors = metadata.get("Authors", "")
        journal = metadata.get("Journal", "")
        abstract = metadata.get("Abstract", "")
        year = metadata.get("Year", "")
        
        # Get user preferences from session state
        prefs = st.session_state.get('user_profile', {})
        preferred_topics = prefs.get('topics', [])
        preferred_authors = prefs.get('authors', [])
        preferred_journals = prefs.get('journals', [])
        
        # Use admin defaults if no user preferences defined
        admin_defaults = {
            'topics': ['physical chemistry', 'biochemistry', 'structural biology', 'protein folding', 'molecular dynamics', 'enzyme kinetics'],
            'authors': [],
            'journals': ['Nature', 'Science', 'Cell', 'PNAS', 'Journal of Physical Chemistry', 'Biochemistry', 'Nature Structural & Molecular Biology']
        }
        
        # Get effective preferences (user preferences or admin defaults)
        effective_topics = preferred_topics if preferred_topics else admin_defaults['topics']
        effective_authors = preferred_authors if preferred_authors else admin_defaults['authors'] 
        effective_journals = preferred_journals if preferred_journals else admin_defaults['journals']
        
        # Create user-preference driven criteria
        criteria = f"""Rate this paper based ENTIRELY on relevance to the user's specific research interests:

        USER'S RESEARCH FOCUS:
        Primary Topics: {effective_topics}
        Preferred Authors: {effective_authors if effective_authors else "No specific author preferences"}
        Preferred Journals: {effective_journals}

        SCORING CRITERIA (User-Interest Driven):
        SCORE 3 (Perfect Match - Highly Relevant):
        - DIRECTLY addresses user's primary topics ({effective_topics})
        - Published in user's preferred journals ({effective_journals})
        - Authored by user's preferred researchers (if specified)
        - High-quality methodology with significant findings
        - Elite journal publication (Nature, Science, Cell, PNAS, field leaders)
        - Major breakthrough or methodological advance in user's areas of interest
        
        SCORE 2 (Good Match - Relevant):
        - SIGNIFICANTLY overlaps with user's topics of interest
        - Published in reputable journals (may include user's preferred journals)
        - Solid methodology and meaningful findings
        - Clear relevance to user's research focus
        - Important contribution to user's field of interest
        
        SCORE 1 (Partial Match - Some Relevance):
        - PARTIALLY relevant to user's research interests
        - Published in decent academic journals
        - Competent methodology with acceptable findings
        - Tangential connection to user's topics
        - May be useful for background or comparative purposes
        
        SCORE 0 (No Match - Not Relevant):
        - NO relevance to user's research interests
        - Does not address any of the user's topic areas
        - Poor methodology or low-quality publication
        - Complete mismatch with user's research focus
        - Not useful for user's research objectives

        CRITICAL: Score based on RELEVANCE TO USER'S INTERESTS, not general academic merit."""
        
        prompt = f"""
        {criteria}

        EFFECTIVE USER INTERESTS (driving the scoring):
        Primary Topics: {effective_topics}
        Preferred Authors: {effective_authors if effective_authors else "No specific preferences"}
        Preferred Journals: {effective_journals}
        
        SCORING INSTRUCTION: Rate based SOLELY on relevance to these specific interests.
        Ignore general academic merit if the paper doesn't match user's research focus.

        Paper Details:
        Title: {title}
        Authors: {authors}
        Journal: {journal}
        Year: {year}
        Abstract: {abstract[:500]}...

        Provide rating in this EXACT format:
        Score: [0-3]
        Tags: [Generate approximately 10 comprehensive tags including: multiple aRT- tags for research topics, multiple aTa- tags for techniques/methods, one aTy- tag for paper type (Review/Experimental/Meta-Analysis/etc), multiple aMe- tags for specific methods/approaches, and one ai-score_X tag where X is your rating score]
        Note: [Brief explanation of rating and significance]
        
        Tag prefixes explanation:
        - aRT- for research topics/subjects (e.g., aRT-protein-folding, aRT-drug-discovery)  
        - aTa- for techniques/technologies (e.g., aTa-NMR-spectroscopy, aTa-machine-learning)
        - aTy- for paper type (e.g., aTy-Review, aTy-Experimental, aTy-Meta-Analysis)
        - aMe- for specific methods/approaches (e.g., aMe-molecular-dynamics, aMe-statistical-analysis)
        - ai-score_X for AI relevance rating (e.g., ai-score_3, ai-score_2)
        """
        
        if not openai_client:
            # Fallback if no OpenAI client available
            return "Score: 1\nTags: [unrated]\nNote: OpenAI API not available"
            
        response = openai_client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        
        return response.choices[0].message.content.strip()
        
    except Exception as e:
        st.warning(f"Rating error: {e}")
        return "Score: 1\nTags: [unrated]\nNote: Rating failed due to API error"

def parse_gpt4_output(rating_text):
    """
    Parse gpt-5-mini rating output into structured format
    Returns: (score_int, keywords_list, note_text)
    """
    try:
        score_int = 1
        keywords = []
        note = ""
        
        lines = rating_text.split('\n')
        
        for line in lines:
            line = line.strip()
            
            if line.startswith("Score:"):
                score_match = re.search(r'Score:\s*(\d+)', line)
                if score_match:
                    score_int = int(score_match.group(1))
                    
            elif line.startswith("Tags:"):
                tag_match = re.search(r'Tags:\s*\[(.*?)\]', line)
                if tag_match:
                    tag_string = tag_match.group(1)
                    keywords = [tag.strip() for tag in tag_string.split(',') if tag.strip()]
                    
            elif line.startswith("Note:"):
                note = line.replace("Note:", "").strip()
        
        # Ensure ai-score tag is present
        ai_score_tag = f"ai-score_{score_int}"
        if not any(tag.startswith("ai-score_") for tag in keywords):
            keywords.append(ai_score_tag)
        
        # Normalize tags to ensure consistent format
        keywords = normalize_tags(keywords)
        
        return score_int, keywords, note
        
    except Exception as e:
        st.warning(f"Output parsing error: {e}")
        return 1, ["parsing-error"], "Failed to parse rating output"

# ============================
# Enhanced Query Generation for Different Sources
# ============================
def openai_boolean_query_with_dates(user_query: str, year_from=None, year_to=None, target_source="PubMed") -> dict:
    """
    Generate optimized queries for different academic search sources.
    Handles both datetime objects and integers for year parameters.
    """
    date_clause = ""
    
    # Extract year from datetime objects if needed
    year_from_int = year_from.year if hasattr(year_from, 'year') else year_from
    year_to_int = year_to.year if hasattr(year_to, 'year') else year_to
    
    if year_from_int and year_to_int:
        if target_source == "PubMed":
            date_clause = f"year:[{year_from_int} TO {year_to_int}]"
        else:  # Semantic Scholar
            date_clause = f"year:{year_from_int}-{year_to_int}"
    elif year_from_int:
        if target_source == "PubMed":
            date_clause = f"year:[{year_from_int} TO *]"
        else:  # Semantic Scholar
            date_clause = f"year:{year_from_int}-"
    elif year_to_int:
        if target_source == "PubMed":
            date_clause = f"year:[* TO {year_to_int}]"
        else:  # Semantic Scholar
            date_clause = f"year:-{year_to_int}"
    
    if target_source == "PubMed":
        prompt = generate_pubmed_query_prompt(user_query, date_clause)
    else:  # Semantic Scholar
        prompt = generate_semantic_scholar_query_prompt(user_query, date_clause)
    
    # Debug: Show what prompt we're sending
    if target_source == "Semantic Scholar":
        print(f"SS Prompt: {prompt[:200]}...")
    
    data = openai_json(prompt)
    
    # Debug: Show what we got back
    if target_source == "Semantic Scholar":
        print(f"SS Response: {data}")
    
    out = {"boolean_query": "", "keywords": [], "year_from": year_from, "year_to": year_to, "source": target_source}
    if isinstance(data, dict):
        out["boolean_query"] = data.get("boolean_query") or ""
        out["keywords"] = data.get("keywords") or []
        # Keep original datetime objects instead of overwriting
        if not year_from:
            out["year_from"] = data.get("year_from")
        if not year_to:
            out["year_to"] = data.get("year_to")
    
    # Add date clause if not already included
    if date_clause and date_clause not in out["boolean_query"]:
        if out["boolean_query"]:
            separator = " AND " if target_source == "PubMed" else " "
            out["boolean_query"] += f"{separator}{date_clause}"
        else:
            out["boolean_query"] = date_clause
    
    return out

def generate_pubmed_query_prompt(user_query: str, date_clause: str) -> str:
    """Generate prompt for PubMed-optimized queries"""
    prompt = (
        "You are an expert in constructing advanced PubMed boolean queries.\n"
        "Given a user research topic or question, do the following:\n"
        "1. Break the input into key concepts (nouns/phrases, not stopwords).\n"
        "2. For each concept, generate a list of synonyms and related terms (including MeSH terms if possible).\n"
        "3. For each concept, group synonyms/related terms with OR, using parentheses and quotes for phrases.\n"
        "4. Combine all concept groups with AND.\n"
        "5. Use best practices for PubMed: avoid stopwords, use quotes for phrases, parentheses for grouping, and do not include extraneous words.\n"
        "6. If a date range is provided, add it as year:[YYYY TO YYYY] at the end.\n"
        "Return a JSON object: {\"boolean_query\": \"...\", \"keywords\": [list of all terms used], \"year_from\": null, \"year_to\": null}\n"
        f"User topic: {user_query}\n"
    )
    if date_clause:
        prompt += f"\nDate range: {date_clause}"
    return prompt

def generate_semantic_scholar_query_prompt(user_query: str, date_clause: str) -> str:
    """Generate prompt for Semantic Scholar-optimized queries"""
    prompt = (
        "You are an expert in constructing Semantic Scholar search queries.\n"
        "Semantic Scholar works best with natural language and specific technical terms.\n"
        "Given a user research topic or question, do the following:\n"
        "1. Extract the core scientific concepts and technical terms.\n"
        "2. Use natural language phrases rather than complex boolean logic.\n"
        "3. Focus on domain-specific terminology and key concepts.\n"
        "4. Avoid overly complex boolean operators - use simple AND when needed.\n"
        "5. Include relevant synonyms but keep the query readable.\n"
        "6. Semantic Scholar performs well with phrases in quotes for exact matches.\n"
        "7. Use field-specific terminology (e.g., 'machine learning', 'neural networks', 'deep learning').\n"
        "8. Keep the query concise but comprehensive.\n"
        "Examples of good Semantic Scholar queries:\n"
        "- 'machine learning healthcare diagnosis'\n"
        "- 'amyloid fibril protein aggregation'\n"
        "- 'transformer attention mechanism NLP'\n"
        "- 'quantum computing algorithms optimization'\n"
        "Return a JSON object: {\"boolean_query\": \"...\", \"keywords\": [list of key terms used], \"year_from\": null, \"year_to\": null}\n"
        f"User topic: {user_query}\n"
    )
    if date_clause:
        prompt += f"\nDate range: {date_clause}"
    return prompt

# ============================
# PROFESSOR'S AI WORKFLOW
# ============================
# ============================
# MULTI-STEP QUERY PROCESSING
# ============================
def render_query_workflow():
    """Render the 3-step query workflow"""
    if "query_step" not in st.session_state:
        st.session_state.query_step = 1
    if "query_step" not in st.session_state:
        st.session_state.query_step = 1
    if "generated_query" not in st.session_state:
        st.session_state.generated_query = ""
    if "query_metadata" not in st.session_state:
        st.session_state.query_metadata = {}

    step_cols = st.columns(3)
    with step_cols[0]:
        if st.session_state.query_step >= 1:
            st.success("✅ Step 1: Natural Language Input")
        else:
            st.info("1️⃣ Step 1: Natural Language Input")
    with step_cols[1]:
        if st.session_state.query_step >= 2:
            st.success("✅ Step 2: AI Query Generation & Execute")
        else:
            st.info("2️⃣ Step 2: AI Query Generation & Execute")
    with step_cols[2]:
        if st.session_state.query_step >= 3:
            st.success("✅ Step 3: Search Results")
        else:
            st.info("3️⃣ Step 3: Search Results")
    st.markdown("---")

    # Step 1: Natural Language Input (always visible)
    st.subheader("Step 1: Describe your research needs")
    user_input = st.text_area(
        "🗣️ Describe what you're looking for in natural language:",
        value=st.session_state.query_metadata.get("original_input", ""),
        height=100,
        placeholder="e.g., 'I need recent papers on machine learning applications in healthcare, particularly focusing on diagnostic imaging'"
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🤖 Generate AI Query"):
            if user_input.strip():
                with st.spinner("🧠 AI is analyzing your query and crafting optimized searches..."):
                    # Step 1: gpt-5-mini classification
                    classi_int, classi_txt = what_is_requested([user_input])
                    classification = classi_int[0]
                    classification_text = classi_txt[0]
                    
                    # Pass date range from UI if available
                    year_from = None
                    year_to = None
                    if 'date_from' in globals() and date_from:
                        year_from = date_from  # Pass full datetime object
                    if 'date_to' in globals() and date_to:
                        year_to = date_to  # Pass full datetime object
                    
                    # Generate optimized queries for different sources
                    queries = {}
                    
                    # Get search source from the main UI
                    current_search_source = st.session_state.get('current_search_source', 'Semantic Scholar')
                    
                    if current_search_source in ("Semantic Scholar", "Both"):
                        semantic_query = openai_boolean_query_with_dates(user_input, year_from, year_to, "Semantic Scholar")
                        queries["Semantic Scholar"] = semantic_query
                    
                    if current_search_source in ("PubMed", "Both"):
                        pubmed_boolean = construct_pubmed_query(user_input, classification)
                        queries["PubMed"] = {
                            "boolean_query": pubmed_boolean,
                            "keywords": user_input.split(),
                            "year_from": year_from,
                            "year_to": year_to,
                            "source": "PubMed"
                        }
                        prof_pubmed_query = construct_pubmed_query(user_input, classification)
                        
                        # Also generate with your existing method for comparison
                        pubmed_query = openai_boolean_query_with_dates(user_input, year_from, year_to, "PubMed")
                        
                        # Use professor's query as primary, yours as fallback
                        enhanced_pubmed_query = {
                            "boolean_query": prof_pubmed_query or pubmed_query.get("boolean_query", ""),
                            "keywords": pubmed_query.get("keywords", []),
                            "year_from": year_from,
                            "year_to": year_to,
                            "source": "PubMed",
                            "classification": classification,
                            "classification_text": classification_text
                        }
                        queries["PubMed"] = enhanced_pubmed_query
                    
                    # Store classification for later use
                    st.session_state.query_classification = classification
                    st.session_state.query_classification_text = classification_text
                    
                    # Use the primary query for the selected source
                    print(f"\n🔍 DEBUG - Primary query selection:")
                    print(f"   current_search_source: {current_search_source}")
                    print(f"   queries available: {list(queries.keys())}")
                    
                    if current_search_source == "Both":
                        primary_query = queries.get("Semantic Scholar", queries.get("PubMed", {}))
                        print(f"   Using 'Both' - primary_query from: {'Semantic Scholar' if 'Semantic Scholar' in queries else 'PubMed'}")
                    else:
                        primary_query = queries.get(current_search_source, {})
                        print(f"   Using single source - primary_query from: {current_search_source}")
                    
                    print(f"   primary_query keys: {list(primary_query.keys()) if primary_query else 'None/Empty'}")
                    print(f"   boolean_query in primary_query: {'boolean_query' in primary_query if primary_query else False}")
                    
                    st.session_state.generated_query = primary_query.get("boolean_query", "")
                    st.session_state.query_metadata = {
                        "original_input": user_input,
                        "keywords": primary_query.get("keywords", []),
                        "year_from": primary_query.get("year_from"),
                        "year_to": primary_query.get("year_to"),
                        "all_queries": queries
                    }
                    
                    # Debug: Print what was stored in session state
                    print(f"\n🔍 DEBUG - Stored in session state:")
                    print(f"   generated_query: '{st.session_state.generated_query}'")
                    print(f"   primary_query: {primary_query}")
                    print(f"   current_search_source: {current_search_source}")
                    print(f"   queries keys: {list(queries.keys())}")
    with col2:
        if st.button("🔄 Reset Step 1"):
            st.session_state.query_metadata = {}
            st.session_state.generated_query = ""
            st.session_state.query_step = 1
            st.rerun()

    st.markdown("---")

    # Step 2: Edit Generated Query (always visible)
    st.subheader("Step 2: Review and Edit AI-Generated Query")
    st.info(f"**Original request:** {st.session_state.query_metadata.get('original_input', '')}")
    edited_query = st.text_area(
        "✏️ Edit the generated search query:",
        value=st.session_state.generated_query,
        height=150,
        help="You can modify the Boolean query. Use AND, OR, NOT operators and quotes for phrases."
    )
    if st.session_state.query_metadata.get("keywords"):
        st.caption(f"**Keywords identified:** {', '.join(st.session_state.query_metadata['keywords'])}")
    if st.session_state.query_metadata.get("year_from") or st.session_state.query_metadata.get("year_to"):
        year_info = f"**Years:** {st.session_state.query_metadata.get('year_from', 'start')} - {st.session_state.query_metadata.get('year_to', 'end')}"
        st.caption(year_info)
    
    # Show source-specific optimized queries
    all_queries = st.session_state.query_metadata.get('all_queries', {})
    if len(all_queries) > 1:
        with st.expander("🔍 Source-Specific Optimized Queries", expanded=False):
            st.info("Each search source has been optimized with different query strategies:")
            st.markdown("• **Semantic Scholar**: Natural language + technical terms")
            st.markdown("• **PubMed**: Boolean logic + MeSH terms + field tags")
            st.markdown("---")
            for source, query_data in all_queries.items():
                st.markdown(f"**{source}:**")
                st.code(query_data.get("boolean_query", ""), language="text")
                if query_data.get("keywords"):
                    st.caption(f"Keywords: {', '.join(query_data['keywords'])}")
                st.markdown("---")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("⬅️ Back to Step 1"):
            st.session_state.query_step = 1
            st.rerun()
    with col2:
        if st.button("🔍 Execute Search"):
            st.session_state.generated_query = edited_query
            st.session_state.query_step = 3
            st.session_state.should_execute_search = True
            st.rerun()
    with col3:
        if st.button("🔄 Reset All"):
            st.session_state.query_step = 1
            st.session_state.generated_query = ""
            st.session_state.query_metadata = {}
            st.rerun()

    st.markdown("---")

    # Step 3: Search Results (only show when search is executed)
    if st.session_state.query_step == 3:
        st.subheader("Step 3: Search Results")
        if st.session_state.generated_query:
            st.write(f"**Final Boolean Query:** {st.session_state.generated_query}")
        else:
            st.info("Please generate and review a query before searching.")

    return st.session_state.query_step == 3, st.session_state.generated_query, st.session_state.get('should_execute_search', False)
def render_text_extraction_workflow():
    """Render the 3-step workflow for text extraction"""
    # Ensure text extraction session state is properly initialized
    if "text_step" not in st.session_state or st.session_state.get("current_mode") != "Paste citation / page text":
        st.session_state.text_step = 1
        st.session_state.current_mode = "Paste citation / page text"
    if "extracted_refs" not in st.session_state:
        st.session_state.extracted_refs = []
    if "edited_refs" not in st.session_state:
        st.session_state.edited_refs = []
    
    # Step indicator with restart button
    col1, col2, col3, col4 = st.columns([2, 2, 2, 1])
    with col1:
        if st.session_state.text_step >= 1:
            st.success("✅ Step 1: Paste Text")
        else:
            st.info("1️⃣ Step 1: Paste Text")
    with col2:
        if st.session_state.text_step >= 2:
            st.success("✅ Step 2: Extract & Edit")
        elif st.session_state.text_step == 2:
            st.warning("🔄 Step 2: Extract & Edit")
        else:
            st.info("2️⃣ Step 2: Extract & Edit")
    with col3:
        if st.session_state.text_step >= 3:
            st.success("✅ Step 3: Process Papers")
        elif st.session_state.text_step == 3:
            st.warning("🔄 Step 3: Process Papers")
        else:
            st.info("3️⃣ Step 3: Process Papers")
    with col4:
        if st.button("🔄 Restart", help="Reset to Step 1", key="restart_btn"):
            st.session_state.text_step = 1
            st.session_state.extracted_refs = []
            st.session_state.edited_refs = []
            st.rerun()
    
    st.markdown("---")
    
    # Step 1: Paste Text (always show if step 1, or if no other steps are active)
    if st.session_state.text_step == 1:
        st.subheader("Step 1: Paste Citation Text")
        paste_text = st.text_area("📋 Paste citation(s) or Google Scholar results / page text:", 
                                 height=220,
                                 placeholder="Paste bibliographic text, Google Scholar results, or any text containing paper references...")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔍 Extract References", disabled=not paste_text.strip()):
                if paste_text.strip():
                    st.session_state.text_step = 2
                    with st.spinner("🧠 AI is extracting references..."):
                        refs = openai_extract_from_text(paste_text)
                        st.session_state.extracted_refs = refs
                        st.session_state.edited_refs = refs.copy()
                    st.rerun()
        
        with col2:
            if st.button("�️ Clear Text", help="Clear the text area"):
                st.rerun()
    
    # Step 2: Edit Extracted References
    elif st.session_state.text_step == 2:
        st.subheader("Step 2: Review and Edit Extracted References")
        
        if not st.session_state.extracted_refs:
            st.warning("No references were extracted. Please go back and try different text.")
            if st.button("⬅️ Back to Step 1"):
                st.session_state.text_step = 1
                st.rerun()
        else:
            st.success(f"Found {len(st.session_state.extracted_refs)} references")
            
            # Allow editing of each reference
            for i, ref in enumerate(st.session_state.extracted_refs):
                with st.expander(f"📄 Reference {i+1}: {ref.get('title', 'Untitled')[:50]}...", expanded=i < 3):
                    col1, col2 = st.columns(2)
                    with col1:
                        title = st.text_input(f"Title {i+1}:", value=ref.get('title', ''), key=f"title_{i}")
                        year = st.number_input(f"Year {i+1}:", value=ref.get('year') or 2024, 
                                             min_value=1900, max_value=2030, key=f"year_{i}")
                    with col2:
                        authors = st.text_input(f"Authors {i+1}:", 
                                              value=', '.join(ref.get('authors', [])) if isinstance(ref.get('authors'), list) else str(ref.get('authors', '')), 
                                              key=f"authors_{i}")
                        doi = st.text_input(f"DOI {i+1}:", value=ref.get('doi', '') or '', key=f"doi_{i}")
                    
                    # Update the edited refs
                    st.session_state.edited_refs[i] = {
                        'title': title,
                        'year': year,
                        'authors': [a.strip() for a in authors.split(',') if a.strip()],
                        'doi': doi
                    }
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("⬅️ Back to Step 1"):
                    st.session_state.text_step = 1
                    st.rerun()
            
            with col2:
                if st.button("🔍 Process Papers"):
                    st.session_state.text_step = 3
                    st.session_state.should_execute_search = True
                    st.rerun()
    
    return st.session_state.text_step == 3, st.session_state.edited_refs
OPERATORS = {"and": "AND", "or": "OR", "not": "NOT"}

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.I)
HTML_TAG_RE = re.compile(r"<[^>]+>")
ARXIV_RE = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5})(?:v\d+)?", re.I)

def build_boolean_query_simple(text: str) -> str:
    """Quick AND-join of comma/;/slash separated tokens; phrases quoted and logicals normalized."""
    q = text.strip()
    tokens = [t.strip() for t in re.split(r",|;|/", q) if t.strip()]
    if len(tokens) >= 2:
        q = " AND ".join([f'"{t}"' if " " in t else t for t in tokens])
    q = re.sub(r"\b(and|or|not)\b", lambda m: OPERATORS[m.group(1).lower()], q, flags=re.I)
    return q

def with_ntu_proxy(url: str | None, style: int = 2) -> str | None:
    if not url:
        return None
    if style == 1:
        return f"https://remotexs.ntu.edu.sg/user/login?dest={url}"
    return f"https://remotexs.ntu.edu.sg/login?url={url}"

def extract_pdf_text(url: str) -> str:
    """Download a PDF and return the first ~5000 chars of text, or empty string if fails."""
    if not url:
        return ""
    try:
        r = requests.get(url, timeout=45)
        r.raise_for_status()
        with fitz.open(stream=io.BytesIO(r.content), filetype="pdf") as doc:
            text = []
            for page in doc:
                text.append(page.get_text())
            return ("\n".join(text))[:5000]
    except Exception:
        return ""

def parse_authors(authors_info: str):
    authors = [a.strip() for a in authors_info.split(",") if a.strip()]
    out = []
    for nm in authors:
        parts = nm.split(" ")
        if len(parts) >= 2:
            out.append({"creatorType": "author", "firstName": " ".join(parts[:-1]), "lastName": parts[-1]})
        else:
            out.append({"creatorType": "author", "name": nm})
    return out

def create_enhanced_zotero_item(title, authors_info, abstract, snippet, url, doi, year, venue, 
                               crossref_data, biorxiv_data, semantic_scholar_data, 
                               tags, collection_id, proxy_url=None):
    """
    Create a comprehensive Zotero item with all available metadata from multiple sources.
    
    Args:
        title: Paper title
        authors_info: Author string  
        abstract: AI-generated or source abstract
        snippet: Original snippet/abstract
        url: Paper URL
        doi: DOI string
        year: Publication year
        venue: Journal/venue name
        crossref_data: Dict from crossref_enrich()
        biorxiv_data: Dict from biorxiv_api_fetch()  
        semantic_scholar_data: Dict with S2 metadata
        tags: List of tags
        collection_id: Zotero collection ID
        proxy_url: Institution proxy URL
    
    Returns:
        Dict representing a comprehensive Zotero item
    """
    # Start with base item structure
    item = {
        'itemType': 'journalArticle',
        'title': title or '',
        'creators': parse_authors(authors_info or '')
    }
    
    # Only add collections if collection_id is provided (empty means save to root)
    if collection_id:
        item['collections'] = [collection_id]
    
    # Abstract - prioritize AI-generated, then snippet
    if abstract:
        item['abstractNote'] = abstract
    elif snippet:
        item['abstractNote'] = snippet
    
    # URL - prefer proxy URL for institutional access
    if proxy_url:
        item['url'] = proxy_url
    elif url:
        item['url'] = url
    
    # DOI
    if doi:
        item['DOI'] = doi
    
    # Publication info - merge from all sources, prioritizing most complete data
    publication_year = None
    publication_date = None
    
    if year:
        publication_year = year
    elif crossref_data and crossref_data.get('year'):
        publication_year = crossref_data['year']
    elif biorxiv_data and biorxiv_data.get('year'):
        publication_year = biorxiv_data['year']
    elif semantic_scholar_data and semantic_scholar_data.get('year'):
        publication_year = semantic_scholar_data['year']
    
    # Try to get more specific publication date
    if semantic_scholar_data and semantic_scholar_data.get('publicationDate'):
        publication_date = semantic_scholar_data['publicationDate']
        item['date'] = publication_date
    elif biorxiv_data and biorxiv_data.get('date'):
        publication_date = biorxiv_data['date'] 
        item['date'] = publication_date
    elif publication_year:
        item['date'] = str(publication_year)
    
    # Journal/venue information (will be set after determining item type)
    journal_name = None
    if crossref_data and crossref_data.get('venue'):
        journal_name = crossref_data['venue']
    elif venue:
        journal_name = venue
    elif biorxiv_data and biorxiv_data.get('venue'):
        journal_name = biorxiv_data['venue']
    elif semantic_scholar_data and semantic_scholar_data.get('venue'):
        journal_name = semantic_scholar_data['venue']
    
    # Store volume/issue/pages for later conditional assignment
    volume_info = None
    issue_info = None
    pages_info = None
    if crossref_data:
        volume_info = crossref_data.get('volume')
        issue_info = crossref_data.get('issue')
        pages_info = crossref_data.get('pages')
    
    # Additional metadata from different sources
    extra_notes = []
    
    # Semantic Scholar data
    if semantic_scholar_data:
        if semantic_scholar_data.get('citationCount') is not None:
            extra_notes.append(f"Citations: {semantic_scholar_data['citationCount']}")
        
        if semantic_scholar_data.get('publicationTypes'):
            pub_types = semantic_scholar_data['publicationTypes']
            if isinstance(pub_types, list) and pub_types:
                extra_notes.append(f"Publication Types: {', '.join(pub_types)}")
    
    # bioRxiv specific data
    if biorxiv_data:
        if biorxiv_data.get('category'):
            extra_notes.append(f"bioRxiv Category: {biorxiv_data['category']}")
        
        if biorxiv_data.get('version'):
            extra_notes.append(f"Version: {biorxiv_data['version']}")
        
        # For preprints, set appropriate item type
        if 'biorxiv' in (biorxiv_data.get('server', '').lower()):
            item['itemType'] = 'preprint'
            # Add archive information
            item['archive'] = 'bioRxiv'
            if biorxiv_data.get('date'):
                item['archiveLocation'] = f"Submitted: {biorxiv_data['date']}"
    
    # Detect preprints from DOI patterns (bioRxiv, medRxiv, arXiv, etc.)
    is_preprint = False
    if doi:
        preprint_patterns = ['10.1101/', '10.48550/arXiv', '10.20944/preprints']
        is_preprint = any(doi.startswith(pattern) for pattern in preprint_patterns)
    
    if is_preprint or item.get('itemType') == 'preprint':
        item['itemType'] = 'preprint'
        # For preprints, use different fields
        if journal_name:
            item['repository'] = journal_name  # Use repository instead of publicationTitle
        # Note: preprints don't typically have volume/issue/pages
    else:
        # For regular journal articles
        if journal_name:
            item['publicationTitle'] = journal_name
        # Add volume, issue, pages for published articles
        if volume_info:
            item['volume'] = str(volume_info)
        if issue_info:
            item['issue'] = str(issue_info)
        if pages_info:
            item['pages'] = str(pages_info)
    
    # Crossref additional data
    if crossref_data:
        crossref_url = crossref_data.get('url')
        if crossref_url and crossref_url != item.get('url'):
            extra_notes.append(f"Publisher URL: {crossref_url}")
    
    # Combine all extra notes
    if extra_notes:
        item['extra'] = '\n'.join(extra_notes)
    
    # Tags
    if tags:
        item['tags'] = [{'tag': t} for t in tags if t]
    
    # Create Zotero attachments for URLs
    attachments = []
    
    # Main URL attachment (always add as link attachment)
    main_url = item.get('url')
    if main_url:
        main_attachment = {
            'itemType': 'attachment',
            'linkMode': 'linked_url',
            'title': 'Full Text',
            'url': main_url,
            'contentType': 'text/html'
        }
        attachments.append(main_attachment)
    
    # PDF URLs as separate attachments
    if semantic_scholar_data and semantic_scholar_data.get('source_data', {}).get('pdf_url'):
        pdf_url = semantic_scholar_data['source_data']['pdf_url']
        if pdf_url and pdf_url != main_url:
            pdf_attachment = {
                'itemType': 'attachment',
                'linkMode': 'linked_url', 
                'title': 'PDF',
                'url': pdf_url,
                'contentType': 'application/pdf'
            }
            attachments.append(pdf_attachment)
    
    if biorxiv_data and biorxiv_data.get('pdf_url'):
        pdf_url = biorxiv_data['pdf_url']
        if pdf_url and pdf_url != main_url and not any(att['url'] == pdf_url for att in attachments):
            pdf_attachment = {
                'itemType': 'attachment',
                'linkMode': 'linked_url',
                'title': 'bioRxiv PDF',
                'url': pdf_url,
                'contentType': 'application/pdf'
            }
            attachments.append(pdf_attachment)
    
    # Add institutional access URL as separate attachment if different from main URL
    if proxy_url and proxy_url != main_url:
        proxy_attachment = {
            'itemType': 'attachment',
            'linkMode': 'linked_url',
            'title': 'Institutional Access',
            'url': proxy_url,
            'contentType': 'text/html'
        }
        attachments.append(proxy_attachment)
    
    # Add attachments to the item
    if attachments:
        item['attachments'] = attachments
    
    # Add language if detected as non-English (useful for international papers)
    if title and not is_likely_english(title):
        item['language'] = 'non-English (detected)'
    
    # Clean up item - remove empty fields
    item = {k: v for k, v in item.items() if v not in (None, "", [])}
    
    return item

def dedupe_results(results):
    seen, out = set(), []
    for r in results:
        doi = (r.get("doi") or "").lower().replace("https://doi.org/", "")
        key = doi or (r.get("url") or r.get("title", "")).lower()
        if key in seen:
            continue
        seen.add(key); out.append(r)
    return out

def _request_json_with_retries(url, *, method="GET", headers=None, params=None, data=None, tries=4, timeout=40):
    delay = SLEEP
    for attempt in range(1, tries + 1):
        try:
            resp = (requests.post(url, headers=headers, params=params, data=data, timeout=timeout)
                    if method == "POST" else
                    requests.get(url, headers=headers, params=params, timeout=timeout))
            if 200 <= resp.status_code < 300:
                return resp.json()
            if 500 <= resp.status_code < 600:
                raise RequestException(f"Server {resp.status_code}")
            resp.raise_for_status()
        except Exception:
            if attempt == tries:
                raise
            sleep(delay)
            delay = min(delay * 2, 3.0)
    return {}

def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i+n]

def _take(results, k):
    return results[:k] if len(results) > k else results

def clean_snippet(text: str) -> str:
    if not text:
        return ""
    text = HTML_TAG_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if DOI_RE.fullmatch(text.replace("doi:", "").strip().lower()):
        return ""
    text = re.sub(r"^doi:\s*10\.\d{4,9}/\S+\s*", "", text, flags=re.I)
    return text

# ============================
# OPENAI (Boolean, extraction, annotation)
# ============================
def openai_json(prompt: str, model: str = "gpt-5-mini") -> dict | list:
    if not OPENAI_API_KEY or not openai_client:
        print("Warning: No valid OpenAI API key found - using fallback")
        return {}
    try:
        resp = openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        txt = resp.choices[0].message.content or ""
        if not txt:
            print("Warning: Empty response from OpenAI API")
            return {}
        try:
            return json.loads(txt)
        except Exception as e:
            print(f"JSON parse error: {e}, trying regex extraction")
            m = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", txt)
            if m:
                return json.loads(m.group(0))
            else:
                print(f"No JSON found in response: {txt[:200]}...")
                return {}
    except Exception as e:
        print(f"OpenAI API error: {e}")
        return {}

def openai_boolean_query(user_query: str) -> dict:
    data = openai_json(f"""
Create a compact Boolean query (use AND/OR/NOT and quotes for phrases) suitable for academic APIs.
Return JSON {{"boolean_query": "...", "keywords": [], "year_from": null, "year_to": null}}
Topic: {user_query}
Priority topics: {prefs.get('topics')}
""")
    out = {"boolean_query": "", "keywords": [], "year_from": None, "year_to": None}
    if isinstance(data, dict):
        out["boolean_query"] = data.get("boolean_query") or ""
        out["keywords"] = data.get("keywords") or []
        out["year_from"] = data.get("year_from")
        out["year_to"] = data.get("year_to")
    return out

def openai_extract_from_text(raw_text: str):
    """
    Extract refs from pasted text (e.g., Google Scholar page).
    Returns list of {title, authors:[...], year, doi?}
    """
    data = openai_json(f"""
You are an academic reference extractor.
From the text below, extract a list of references as JSON array. Each object must have:
- "title" (string)
- "authors" (list of names)
- "year" (int if available else null)
- "doi" (string DOI without https://doi.org/ if present else null)

Text:
{raw_text}

Return strictly a JSON array.
""")
    out = []
    if isinstance(data, list):
        for it in data:
            if not isinstance(it, dict):
                continue
            title = (it.get("title") or "").strip()
            if not title:
                continue
            authors = it.get("authors") or []
            if isinstance(authors, str):
                authors = [a.strip() for a in authors.split(",") if a.strip()]
            year = it.get("year")
            doi  = it.get("doi")
            if isinstance(doi, str):
                m = DOI_RE.search(doi)
                doi = m.group(0) if m else doi.strip()
            out.append({"title": title, "authors": authors, "year": year, "doi": doi})
    return out

def openai_annotate_paper(title, authors, snippet, pdf_text, url, user_query):
    """
    Return: abstract (10 to 15 sentences), tags [aRT..., aTa..., aTy..., aMe..., ai score-n], score3 (0..3)
    """
    # Get user preferences from session state
    prefs = st.session_state.get('user_profile', {})
    
    # Use admin defaults if no user preferences defined
    admin_defaults = {
        'topics': ['physical chemistry', 'biochemistry', 'structural biology', 'protein folding', 'molecular dynamics', 'enzyme kinetics'],
        'authors': [],
        'journals': ['Nature', 'Science', 'Cell', 'PNAS', 'Journal of Physical Chemistry', 'Biochemistry', 'Nature Structural & Molecular Biology']
    }
    
    # Get effective preferences (user preferences or admin defaults)
    effective_topics = prefs.get('topics', []) if prefs.get('topics') else admin_defaults['topics']
    effective_authors = prefs.get('authors', []) if prefs.get('authors') else admin_defaults['authors']
    effective_journals = prefs.get('journals', []) if prefs.get('journals') else admin_defaults['journals']
    
    prompt = f"""
You are an academic assistant focused on the user's specific research interests. Analyze this paper and return JSON with keys:
IMPORTANT: All output must be in English regardless of the source document language.

- "abstract": a 10 to 15 sentence abstract in English (self-contained; no references; factual only)
- "tags": list of strings in English with REQUIRED prefixes:
    * aRT-Research Topic (1-2 precise tags, e.g., "aRT-Protein Folding", "aRT-Drug Discovery")
    * aTa-Topic Tags (3-6 specific tags, e.g., "aTa-Machine Learning", "aTa-Structural Biology")  
    * aTy-Paper Type (e.g., "aTy-Review Article", "aTy-Experimental Study", "aTy-Meta Analysis")
    * aMe-Methods (key methods, e.g., "aMe-Molecular Dynamics", "aMe-Statistical Analysis")
    * Plus exactly one tag "ai score-N" where N is 0..3
  
IMPORTANT: Use hyphens (-) NOT colons (:) in tags. Format: "aRT-Topic Name" NOT "aRT:Topic Name"

- "score3": integer 0..3 based on RIGOROUS ACADEMIC EVALUATION:

USER'S SPECIFIC RESEARCH INTERESTS:
Primary Topics: {effective_topics}
Preferred Authors: {effective_authors if effective_authors else "No specific author preferences"}
Preferred Journals: {effective_journals}

SCORING CRITERIA (Based ENTIRELY on User Interest Relevance):
SCORE 3 (Perfect Match - Exactly What User Needs):
- DIRECTLY addresses user's primary research topics: {effective_topics}
- Published in user's preferred journals: {effective_journals}
- Authored by user's preferred researchers (if specified): {effective_authors}
- High-quality methodology within user's field of interest
- Perfect alignment with user's research objectives

SCORE 2 (Good Match - Highly Relevant to User):
- SIGNIFICANTLY overlaps with user's topics: {effective_topics}
- Strong relevance to user's research focus
- Published in reputable journals (may include user's preferred ones)
- Good methodology and meaningful findings in user's area
- Would be valuable for user's research

SCORE 1 (Partial Match - Some User Relevance):
- PARTIALLY relevant to user's research interests
- Tangential connection to user's topics
- May provide useful background or comparative context
- Acceptable quality but limited direct applicability
- Might be useful for comprehensive literature review

SCORE 0 (No Match - Not Useful for User):
- NO relevance to user's research interests: {effective_topics}
- Does not address any of user's topic areas
- Would not be useful for user's research objectives
- Complete mismatch with user's focus
- Not worth user's time to read

Paper info:
Title: {title}
Authors: {authors}
Context: {snippet if snippet is not None else ''}
PDF: {pdf_text if pdf_text is not None else ''}
URL: {url if url is not None else ''}

EFFECTIVE USER INTERESTS (the primary scoring factors):
Primary Topics: {effective_topics}
Preferred Authors: {effective_authors if effective_authors else "No specific preferences"}
Preferred Journals: {effective_journals}

CRITICAL SCORING INSTRUCTIONS:
1. PRIMARY FACTOR: How well does this paper address the user's topics: {effective_topics}?
2. SECONDARY FACTOR: Is it in user's preferred journals: {effective_journals}?
3. TERTIARY FACTOR: Is it by user's preferred authors: {effective_authors}?
4. Score 3 ONLY if paper perfectly matches user's research interests
5. Score 0 if paper has NO relevance to user's topics
6. Ignore general academic prestige if it doesn't match user's interests

CRITICAL: Respond ONLY in English. Output JSON only.
"""
    data = openai_json(prompt)
    abstract, tags, score3_val = "", [], 0
    if isinstance(data, dict):
        abstract = data.get("abstract", "") or ""
        raw_tags = data.get("tags", []) or []
        score3_val = data.get("score3", 0) or 0
        try:
            score3_val = int(score3_val)
        except Exception:
            score3_val = 0
        tags = [t for t in raw_tags if isinstance(t, str)]
    
    # Normalize tag formatting: ensure consistent hyphen format
    tags = normalize_tags(tags)
    
    # ensure ai score-n tag exists and matches score3
    score_tag = f"ai score-{max(0, min(3, score3_val))}"
    if score_tag not in tags:
        tags.append(score_tag)
    return abstract.strip(), tags, max(0, min(3, score3_val))

# ============================
# SEARCH PROVIDERS (S2 + PubMed) + Crossref + Google fallback
# ============================
# Enhanced search functions with date filtering
def search_semantic_scholar_with_dates(query, limit=10, year_from=None, year_to=None):
    """Semantic Scholar search with date filtering"""
    # Validate query - cannot be empty or None
    if not query or not query.strip():
        st.warning("⚠️ Search query cannot be empty")
        return []
    
    query = query.strip()
    
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    if SEMANTIC_SCHOLAR_API_KEY:
        headers = {"x-api-key": SEMANTIC_SCHOLAR_API_KEY}
    else:
        headers = {}
    params = {
        "query": query,
        "limit": limit,
        "fields": "title,authors,url,abstract,openAccessPdf,externalIds,venue,year,citationCount,publicationDate,publicationTypes"
    }
    
    # Add year filtering if specified
    if year_from:
        params["year"] = f"{year_from.year}-"
    if year_to:
        if year_from:
            params["year"] = f"{year_from.year}-{year_to.year}"
        else:
            params["year"] = f"-{year_to.year}"
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        st.error(f"Semantic Scholar error: {e}")
        return []

    results = []
    for paper in (data or {}).get("data", []) or []:
        # Additional date filtering for more precision
        if year_from or year_to:
            paper_year = paper.get("year")
            if paper_year:
                if year_from and paper_year < year_from.year:
                    continue
                if year_to and paper_year > year_to.year:
                    continue
        
        doi = None
        if isinstance(paper.get("externalIds"), dict):
            doi = paper["externalIds"].get("DOI")
        results.append({
            "title": paper.get("title", ""),
            "url": paper.get("url", "") or (f"https://doi.org/{doi}" if doi else ""),
            "authors_info": ", ".join([a.get("name", "") for a in paper.get("authors", [])]),
            "snippet": clean_snippet(paper.get("abstract", "") or ""),
            "pdf_url": (paper.get("openAccessPdf") or {}).get("url", ""),
            "doi": doi,
            "venue": paper.get("venue"),
            "year": paper.get("year"),
            "citationCount": paper.get("citationCount"),
            "publicationDate": paper.get("publicationDate"),
            "publicationTypes": paper.get("publicationTypes"),
        })
    return results

def search_semantic_scholar(query, limit=10):
    """Compatibility function for existing code"""
    return search_semantic_scholar_with_dates(query, limit)

def search_pubmed_with_dates(query, limit=10, year_from=None, year_to=None):
    """PubMed search with date filtering and improved error handling"""
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    
    # Add date filtering to query
    term = (query or "")[:300]
    if year_from or year_to:
        if year_from and year_to:
            # Use more reliable PubMed date format - just year range for better compatibility
            date_range = f'("{year_from.year}"[Date - Publication] : "{year_to.year}"[Date - Publication])'
        elif year_from:
            date_range = f'"{year_from.year}"[Date - Publication] : "3000"[Date - Publication]'
        else:  # year_to only
            date_range = f'"1900"[Date - Publication] : "{year_to.year}"[Date - Publication]'
        
        term = f"({term}) AND {date_range}"
    
    es_params = {"db": "pubmed", "term": term, "retmode": "json", "retmax": limit, "email": NCBI_EMAIL}
    if NCBI_API_KEY:
        es_params["api_key"] = NCBI_API_KEY
    
    # Improved error handling with retries and longer timeout
    for attempt in range(3):
        try:
            timeout = 45 + (attempt * 15)  # Increase timeout on each retry
            es = requests.get(f"{base}/esearch.fcgi", params=es_params, timeout=timeout).json()
            break
        except requests.exceptions.ConnectTimeout:
            if attempt == 2:  # Last attempt
                st.warning(f"⚠️ PubMed connection timeout after {attempt + 1} attempts. This can happen due to network issues or high server load. Skipping PubMed search.")
                return []
            st.info(f"🔄 PubMed connection timeout, retrying... (attempt {attempt + 1}/3)")
            sleep(2 + attempt)  # Exponential backoff
        except requests.exceptions.RequestException as e:
            if attempt == 2:  # Last attempt
                st.warning(f"⚠️ PubMed connection error: {str(e)[:100]}... Skipping PubMed search.")
                return []
            st.info(f"🔄 PubMed connection error, retrying... (attempt {attempt + 1}/3)")
            sleep(2 + attempt)
        except Exception as e:
            st.warning(f"⚠️ PubMed ESearch error: {str(e)[:100]}... Skipping PubMed search.")
            return []

    ids = (es.get("esearchresult", {}) or {}).get("idlist", []) or []
    if not ids:
        return []

    # ESummary (basic metadata) with retry logic
    sum_params = {"db": "pubmed", "id": ",".join(ids), "retmode": "json", "email": NCBI_EMAIL}
    if NCBI_API_KEY:
        sum_params["api_key"] = NCBI_API_KEY
    
    # ESummary with retry logic
    for attempt in range(3):
        try:
            timeout = 45 + (attempt * 15)
            sm = requests.get(f"{base}/esummary.fcgi", params=sum_params, timeout=timeout).json()
            break
        except requests.exceptions.ConnectTimeout:
            if attempt == 2:
                st.warning("⚠️ PubMed metadata timeout, returning basic results only")
                return []
            sleep(2 + attempt)
        except Exception as e:
            if attempt == 2:
                st.warning(f"⚠️ PubMed ESummary error: {str(e)[:100]}...")
                return []
            sleep(2 + attempt)

    # EFetch to get abstracts and DOIs (XML) — best effort with timeout handling
    abstracts = {}
    dois = {}
    try:
        ef_params = {"db": "pubmed", "retmode": "xml", "email": NCBI_EMAIL}
        if NCBI_API_KEY:
            ef_params["api_key"] = NCBI_API_KEY
        ef = requests.post(f"{base}/efetch.fcgi", params=ef_params, data={"id": ",".join(ids)}, timeout=60)
        ef.raise_for_status()
        root = ET.fromstring(ef.text)
        for art in root.findall(".//PubmedArticle"):
            pmid = art.findtext(".//PMID")
            
            # Extract abstract
            abst_nodes = art.findall(".//Abstract/AbstractText")
            abs_text = " ".join((n.text or "") for n in abst_nodes).strip()
            if abs_text:
                abstracts[pmid] = clean_snippet(abs_text)
            
            # Extract DOI from ArticleIdList
            doi = None
            for article_id in art.findall(".//ArticleIdList/ArticleId"):
                if article_id.get("IdType") == "doi":
                    doi = article_id.text
                    break
            if doi:
                dois[pmid] = doi
    except Exception:
        pass

    out, block = [], sm.get("result", {}) or {}
    for pmid in ids[:limit]:
        r = block.get(pmid, {}) or {}
        jrnl = r.get("fulljournalname") or r.get("source")
        # year parsing
        year = None
        try:
            dp = r.get("pubdate") or ""
            m = re.search(r"\b(19|20)\d{2}\b", dp)
            if m:
                year = int(m.group(0))
        except Exception:
            pass

        out.append({
            "title": r.get("title", ""),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "authors_info": ", ".join([a.get("name","") for a in (r.get("authors") or [])]) if isinstance(r.get("authors", []), list) else "",
            "snippet": abstracts.get(pmid) or clean_snippet(r.get("source", "") or ""),
            "pdf_url": "",
            "doi": dois.get(pmid),  # Use extracted DOI instead of None
            "venue": jrnl,
            "year": year,
            "citationCount": None,
            "publicationDate": r.get("pubdate"),
            "publicationTypes": r.get("pubtype"),
        })
    return out

def semantic_scholar_by_doi(doi: str):
    if not doi:
        return None
    url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
    headers = {"x-api-key": SEMANTIC_SCHOLAR_API_KEY} if SEMANTIC_SCHOLAR_API_KEY else {}
    params = {"fields": "title,authors,url,abstract,openAccessPdf,externalIds,venue,year,citationCount,publicationDate,publicationTypes"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        p = r.json()
        return {
            "title": p.get("title", ""),
            "url": p.get("url", "") or (f"https://doi.org/{doi}"),
            "authors_info": ", ".join([a.get("name", "") for a in p.get("authors", [])]),
            "snippet": clean_snippet(p.get("abstract", "") or ""),
            "pdf_url": (p.get("openAccessPdf") or {}).get("url", ""),
            "doi": (p.get("externalIds") or {}).get("DOI") or doi,
            "venue": p.get("venue"),
            "year": p.get("year"),
            "citationCount": p.get("citationCount"),
            "publicationDate": p.get("publicationDate"),
            "publicationTypes": p.get("publicationTypes"),
        }
    except Exception:
        return None

def search_pubmed(query, limit=10):
    """
    Simple, robust PubMed: GET ESearch + ESummary + (best-effort) EFetch abstracts; term capped to 300 chars.
    Improved with better timeout and retry logic.
    """
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    term = (query or "")[:300]  # PubMed truncation
    es_params = {"db": "pubmed", "term": term, "retmode": "json", "retmax": limit, "email": NCBI_EMAIL}
    if NCBI_API_KEY:
        es_params["api_key"] = NCBI_API_KEY
    
    # Improved error handling with retries and longer timeout
    for attempt in range(3):
        try:
            timeout = 45 + (attempt * 15)  # Increase timeout on each retry
            es = requests.get(f"{base}/esearch.fcgi", params=es_params, timeout=timeout).json()
            break
        except requests.exceptions.ConnectTimeout:
            if attempt == 2:  # Last attempt
                st.warning(f"⚠️ PubMed connection timeout after {attempt + 1} attempts. This can happen due to network issues or high server load. Skipping PubMed search.")
                return []
            st.info(f"🔄 PubMed connection timeout, retrying... (attempt {attempt + 1}/3)")
            sleep(2 + attempt)  # Exponential backoff
        except requests.exceptions.RequestException as e:
            if attempt == 2:  # Last attempt
                st.warning(f"⚠️ PubMed connection error: {str(e)[:100]}... Skipping PubMed search.")
                return []
            st.info(f"🔄 PubMed connection error, retrying... (attempt {attempt + 1}/3)")
            sleep(2 + attempt)
        except Exception as e:
            st.warning(f"⚠️ PubMed ESearch error: {str(e)[:100]}... Skipping PubMed search.")
            return []

    ids = (es.get("esearchresult", {}) or {}).get("idlist", []) or []
    if not ids:
        return []

    # ESummary (basic metadata) with retry logic
    sum_params = {"db": "pubmed", "id": ",".join(ids), "retmode": "json", "email": NCBI_EMAIL}
    if NCBI_API_KEY:
        sum_params["api_key"] = NCBI_API_KEY
    
    for attempt in range(3):
        try:
            timeout = 45 + (attempt * 15)
            sm = requests.get(f"{base}/esummary.fcgi", params=sum_params, timeout=timeout).json()
            break
        except requests.exceptions.ConnectTimeout:
            if attempt == 2:
                st.warning("⚠️ PubMed metadata timeout, returning basic results only")
                return []
            sleep(2 + attempt)
        except Exception as e:
            if attempt == 2:
                st.warning(f"⚠️ PubMed ESummary error: {str(e)[:100]}...")
                return []
            sleep(2 + attempt)

    # EFetch to get abstracts and DOIs (XML) — best effort
    abstracts = {}
    dois = {}
    try:
        ef_params = {"db": "pubmed", "retmode": "xml", "email": NCBI_EMAIL}
        if NCBI_API_KEY:
            ef_params["api_key"] = NCBI_API_KEY
        ef = requests.post(f"{base}/efetch.fcgi", params=ef_params, data={"id": ",".join(ids)}, timeout=40)
        ef.raise_for_status()
        root = ET.fromstring(ef.text)
        for art in root.findall(".//PubmedArticle"):
            pmid = art.findtext(".//PMID")
            
            # Extract abstract
            abst_nodes = art.findall(".//Abstract/AbstractText")
            abs_text = " ".join((n.text or "") for n in abst_nodes).strip()
            if abs_text:
                abstracts[pmid] = clean_snippet(abs_text)
            
            # Extract DOI from ArticleIdList
            doi = None
            for article_id in art.findall(".//ArticleIdList/ArticleId"):
                if article_id.get("IdType") == "doi":
                    doi = article_id.text
                    break
            if doi:
                dois[pmid] = doi
    except Exception:
        pass

    out, block = [], sm.get("result", {}) or {}
    for pmid in ids[:limit]:
        r = block.get(pmid, {}) or {}
        jrnl = r.get("fulljournalname") or r.get("source")
        # year parsing
        year = None
        try:
            dp = r.get("pubdate") or ""
            m = re.search(r"\b(19|20)\d{2}\b", dp)
            if m:
                year = int(m.group(0))
        except Exception:
            pass

        out.append({
            "title": r.get("title", ""),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "authors_info": ", ".join([a.get("name","") for a in (r.get("authors") or [])]) if isinstance(r.get("authors", []), list) else "",
            "snippet": abstracts.get(pmid) or clean_snippet(r.get("source", "") or ""),
            "pdf_url": "",
            "doi": dois.get(pmid),  # Use extracted DOI instead of None
            "venue": jrnl,
            "year": year,
            "citationCount": None,
            "publicationDate": r.get("pubdate"),
            "publicationTypes": r.get("pubtype"),
        })
    return out

def fetch_pubmed_metadata(pmid):
    """
    Fetch detailed metadata for a single PMID
    Returns structured metadata dict compatible with professor's workflow
    """
    try:
        base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
        
        # First get basic info
        esummary_params = {"db": "pubmed", "retmode": "json", "id": pmid}
        if NCBI_API_KEY:
            esummary_params["api_key"] = NCBI_API_KEY
            
        summary_resp = requests.get(f"{base}/esummary.fcgi", params=esummary_params, timeout=30)
        summary_resp.raise_for_status()
        summary_data = summary_resp.json()
        
        result = summary_data.get("result", {}).get(pmid, {})
        if not result:
            return None
            
        # Get detailed info with abstract
        efetch_params = {"db": "pubmed", "retmode": "xml", "id": pmid}
        if NCBI_API_KEY:
            efetch_params["api_key"] = NCBI_API_KEY
            
        detail_resp = requests.get(f"{base}/efetch.fcgi", params=efetch_params, timeout=30)
        detail_resp.raise_for_status()
        
        # Parse XML for abstract
        root = ET.fromstring(detail_resp.text)
        abstract_nodes = root.findall(".//Abstract/AbstractText")
        abstract = " ".join((n.text or "") for n in abstract_nodes).strip()
        
        # Parse DOI
        doi_nodes = root.findall(".//ArticleId[@IdType='doi']")
        doi = doi_nodes[0].text if doi_nodes else ""
        
        # Build metadata dict in expected format
        authors_list = result.get("authors", [])
        if isinstance(authors_list, list):
            authors_str = ", ".join([a.get("name", "") for a in authors_list])
        else:
            authors_str = str(authors_list)
            
        metadata = {
            "Title": result.get("title", ""),
            "Authors": authors_str,
            "Journal": result.get("fulljournalname", "") or result.get("source", ""),
            "Year": result.get("pubdate", "").split()[0] if result.get("pubdate") else "",
            "Abstract": abstract,
            "DOI": doi,
            "PMID": pmid,
            "URL": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "Volume": result.get("volume", ""),
            "Issue": result.get("issue", ""),
            "Pages": result.get("pages", ""),
            "PublicationType": result.get("pubtype", [])
        }
        
        return metadata
        
    except Exception as e:
        st.warning(f"Error fetching PMID {pmid}: {e}")
        return None

# ---------- Crossref enrichment (if DOI is known) ----------
def crossref_enrich(doi: str) -> dict:
    if not doi:
        return {}
    url = f"https://api.crossref.org/works/{doi}"
    try:
        data = _request_json_with_retries(url, timeout=30)
        msg = (data or {}).get("message", {})
        if not msg:
            return {}
        title = (msg.get("title") or [""])[0]
        journal = (msg.get("container-title") or [""])[0]
        date_parts = (msg.get("issued") or {}).get("date-parts", [[]])
        year = date_parts[0][0] if date_parts and date_parts[0] else None
        volume = msg.get("volume")
        issue = msg.get("issue")
        page = msg.get("page")
        url = msg.get("URL")
        authors = []
        for a in msg.get("author", []) or []:
            nm = f"{a.get('given','')} {a.get('family','')}".strip()
            if nm: authors.append(nm)
        return {
            "title": title,
            "venue": journal,
            "year": year,
            "volume": volume,
            "issue": issue,
            "pages": page,
            "url": url,
            "authors_info": ", ".join(authors),
        }
    except Exception:
        return {}

def biorxiv_api_fetch(doi: str) -> dict:
    """Fetch paper metadata from bioRxiv API using DOI.
    
    Args:
        doi: DOI in format like "10.1101/2025.09.22.677917"
    
    Returns:
        dict with title, abstract, authors, date, etc.
    """
    if not doi or not doi.startswith("10.1101/"):
        return {}
    
    try:
        # Clean DOI: remove version suffixes (e.g., v1, v2, etc.) that bioRxiv API doesn't recognize
        clean_doi = doi
        if 'v' in doi:
            import re
            # Remove version suffix like v1, v2, v3, etc.
            clean_doi = re.sub(r'v\d+$', '', doi)
        
        # bioRxiv API format: /details/{server}/{doi}/na/json
        api_url = f"https://api.biorxiv.org/details/biorxiv/{clean_doi}/na/json"
        
        response = requests.get(api_url, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        if data.get("collection") and len(data["collection"]) > 0:
            collection = data["collection"]
            
            # If original DOI had version suffix, try to find that specific version
            requested_version = None
            if 'v' in doi:
                import re
                version_match = re.search(r'v(\d+)$', doi)
                if version_match:
                    requested_version = version_match.group(1)
            
            # Find the requested version or use the most recent (last in collection)
            paper = collection[-1]  # Default to most recent version
            if requested_version:
                for p in collection:
                    if p.get("version") == requested_version:
                        paper = p
                        break
            
            # Extract authors
            authors_list = []
            if paper.get("authors"):
                # Authors come as "Last, F.; Last2, F2;" format
                author_parts = [a.strip() for a in paper["authors"].split(";") if a.strip()]
                for author in author_parts:
                    if author:
                        # Convert "Last, F." to "F. Last" format
                        if "," in author:
                            parts = author.split(",", 1)
                            if len(parts) == 2:
                                last_name = parts[0].strip()
                                first_name = parts[1].strip()
                                authors_list.append(f"{first_name} {last_name}")
                            else:
                                authors_list.append(author)
                        else:
                            authors_list.append(author)
            
            # Extract year from date
            paper_date = paper.get("date", "")
            year = None
            if paper_date and len(paper_date) >= 4:
                try:
                    year = int(paper_date[:4])
                except:
                    pass
            
            result = {
                "title": paper.get("title", "").strip(),
                "abstract": paper.get("abstract", "").strip(),
                "authors_info": "; ".join(authors_list),
                "venue": "bioRxiv",
                "year": year,
                "date": paper_date,
                "doi": doi,
                "url": f"https://www.biorxiv.org/content/{doi}",
                "pdf_url": f"https://www.biorxiv.org/content/{doi}.full.pdf",
                "category": paper.get("category", ""),
                "server": "biorxiv",
                "snippet": paper.get("abstract", "").strip()[:1200]  # Use abstract as snippet
            }
            
            return result
            
    except Exception as e:
        # If API fails, return empty dict (will fall back to PDF parsing)
        print(f"bioRxiv API error: {e}")
        return {}
    
    return {}

def universal_doi_lookup(doi: str) -> dict:
    """Universal DOI lookup that works for any publisher using Crossref API.
    
    Args:
        doi: DOI string like "10.1038/nature12373" or "10.1101/2025.09.22.677917"
    
    Returns:
        dict with enhanced metadata including abstract when available
    """
    if not doi:
        return {}
    
    # Clean the DOI
    doi = doi.strip()
    if doi.startswith("https://doi.org/"):
        doi = doi.replace("https://doi.org/", "")
    elif doi.startswith("http://dx.doi.org/"):
        doi = doi.replace("http://dx.doi.org/", "")
    
    # First, try bioRxiv API for bioRxiv papers (has abstracts)
    if doi.startswith("10.1101/"):
        biorxiv_result = biorxiv_api_fetch(doi)
        if biorxiv_result:
            return biorxiv_result
    
    # For all other DOIs, use Crossref API
    try:
        crossref_data = crossref_enrich(doi)
        if not crossref_data:
            return {}
        
        # Try to get abstract from Crossref (some publishers provide it)
        url = f"https://api.crossref.org/works/{doi}"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        full_data = response.json()
        
        work = full_data.get("message", {})
        abstract = work.get("abstract", "")
        
        # Clean HTML tags from abstract if present
        if abstract:
            import re
            abstract = re.sub(r'<[^>]+>', '', abstract)
            abstract = abstract.strip()
        
        # Get additional metadata
        subject_areas = work.get("subject", [])
        publisher = work.get("publisher", "")
        license_info = work.get("license", [])
        
        # Construct enhanced result
        result = {
            "title": crossref_data.get("title", ""),
            "abstract": abstract,  # May be empty for some publishers
            "authors_info": crossref_data.get("authors_info", ""),
            "venue": crossref_data.get("venue", ""),
            "year": crossref_data.get("year"),
            "doi": doi,
            "url": f"https://doi.org/{doi}",
            "pdf_url": "",  # Crossref doesn't provide direct PDF links
            "publisher": publisher,
            "subject_areas": subject_areas,
            "snippet": abstract[:1200] if abstract else "",
            "volume": crossref_data.get("volume"),
            "issue": crossref_data.get("issue"),
            "pages": crossref_data.get("pages")
        };
        
        return result
        
    except Exception as e:
        print(f"Universal DOI lookup error: {e}")
        # Fall back to basic crossref data
        return crossref_enrich(doi)

def extract_doi_from_url(url: str) -> str:
    """Extract DOI from various academic URL formats.
    
    Examples:
        https://doi.org/10.1038/nature12373 -> 10.1038/nature12373
        https://www.nature.com/articles/nature12373 -> 10.1038/nature12373 (if detectable)
        https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0123456 -> 10.1371/journal.pone.0123456
    """
    if not url:
        return ""
    
    import re
    
    # Direct DOI URLs
    doi_match = re.search(r'doi\.org/(.+)', url)
    if doi_match:
        return doi_match.group(1)
    
    # dx.doi.org URLs
    dx_doi_match = re.search(r'dx\.doi\.org/(.+)', url)
    if dx_doi_match:
        return dx_doi_match.group(1)
    
    # bioRxiv URLs
    if 'biorxiv.org' in url:
        return extract_biorxiv_doi(url)
    
    # General DOI pattern in URL
    general_doi_match = re.search(r'10\.\d+/[^\s&?]+', url)
    if general_doi_match:
        doi = general_doi_match.group(0)
        # Clean common URL artifacts
        doi = doi.rstrip('.html').rstrip('.pdf').rstrip('/')
        return doi
    
    # Publisher-specific patterns
    
    # Nature articles: https://www.nature.com/articles/nature12373
    if 'nature.com/articles/' in url:
        article_match = re.search(r'/articles/([^/?]+)', url)
        if article_match:
            article_id = article_match.group(1)
            return f"10.1038/{article_id}"
    
    # Science articles: https://science.org/doi/10.1126/science.abc123
    if 'science.org/doi/' in url:
        science_doi_match = re.search(r'/doi/(.+)', url)
        if science_doi_match:
            return science_doi_match.group(1)
    
    # PLOS ONE: https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0123456
    if 'plos.org' in url and 'id=' in url:
        plos_match = re.search(r'id=(10\.1371/[^&\s]+)', url)
        if plos_match:
            return plos_match.group(1)
    
    # IEEE: https://ieeexplore.ieee.org/document/123456 (would need more complex lookup)
    # Springer: https://link.springer.com/article/10.1007/s12345-021-01234-5
    if 'springer.com' in url:
        springer_match = re.search(r'/article/(10\.1007/[^/?]+)', url)
        if springer_match:
            return springer_match.group(1)
    
    # Elsevier/ScienceDirect: More complex, often needs additional lookup
    
    return ""

# ---------- URL / PDF handling ----------
def fetch_url_and_guess_pdf(url: str) -> tuple[bool, str]:
    """Return (is_pdf, text). Detect PDF by header, extension, or magic bytes.
       If PDF, extract up to 8000 chars; else return (False, "")."""
    try:
        # Special handling for bioRxiv URLs
        if 'biorxiv.org' in url.lower():
            url = optimize_biorxiv_url(url)
        
        r = requests.get(url, timeout=45, allow_redirects=True)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "").lower()
        content = r.content

        # PDF detection: by header, extension, or magic number
        is_pdf = (
            "pdf" in ctype
            or url.lower().endswith(".pdf")
            or content.startswith(b"%PDF")
        )

        if is_pdf:
            with fitz.open(stream=io.BytesIO(content), filetype="pdf") as doc:
                text = []
                for page in doc:
                    page_text = page.get_text()
                    # Clean and process the text
                    page_text = clean_pdf_text(page_text)
                    text.append(page_text)
                
                full_text = "\n".join(text)
                # Focus on the most relevant parts for annotation
                processed_text = extract_relevant_pdf_content(full_text)
                return True, processed_text[:8000]

        return False, ""
    except Exception:
        return False, ""

def extract_biorxiv_doi(url: str) -> str:
    """Extract DOI from bioRxiv URL.
    
    Examples:
        https://www.biorxiv.org/content/10.1101/2025.09.22.677917v1.full.pdf
        -> 10.1101/2025.09.22.677917
    """
    if 'biorxiv.org' not in url:
        return ""
    
    # Pattern: /content/10.1101/YYYY.MM.DD.IDENTIFIERvN
    import re
    match = re.search(r'/content/(10\.1101/\d{4}\.\d{2}\.\d{2}\.\d+(?:v\d+)?)', url)
    if match:
        return match.group(1)
    
    return ""

def optimize_biorxiv_url(url: str) -> str:
    """Optimize bioRxiv URLs to get the best PDF version."""
    if 'biorxiv.org' not in url:
        return url
    
    # Handle different bioRxiv URL formats:
    # 1. Landing page: https://www.biorxiv.org/content/10.1101/2025.09.22.677917v1
    # 2. Already PDF: https://www.biorxiv.org/content/10.1101/2025.09.22.677917v1.full.pdf
    # 3. Without version: https://www.biorxiv.org/content/10.1101/2025.09.22.677917
    
   
    
    if '/content/10.1101/' in url:
        # Extract the DOI part with potential version number
        import re
        doi_match = re.search(r'/content/(10\.1101/\d{4}\.\d{2}\.\d{2}\.\d+(?:v\d+)?)', url)
        if doi_match:
            doi_part = doi_match.group(1)
            # Ensure we have the full PDF URL
            optimized_url = f"https://www.biorxiv.org/content/{doi_part}.full.pdf"
            return optimized_url
    
    return url

def clean_pdf_text(text: str) -> str:
    """Clean PDF text by removing artifacts and improving readability."""
    if not text:
        return ""
    
    # Remove excessive whitespace and normalize line breaks
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n', text)
    
    # Remove common PDF artifacts
    text = re.sub(r'^\d+\s*$', '', text, flags=re.MULTILINE)  # Page numbers
    text = re.sub(r'^[^\w\s]{1,5}$', '', text, flags=re.MULTILINE)  # Symbol lines
    text = re.sub(r'\b[A-Z]{2,}\b(?=\s[A-Z]{2,})', '', text)  # All caps sequences
    
    # Clean up spacing
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text

def extract_relevant_pdf_content(full_text: str) -> str:
    """Extract the most relevant content from PDF text for annotation."""
    if not full_text:
        return ""
    
    lines = [line.strip() for line in full_text.split('\n') if line.strip()]
    relevant_sections = []
    
    # Find title (usually one of the first few lines)
    title_candidates = []
    for i, line in enumerate(lines[:10]):
        if len(line) > 20 and len(line) < 200 and not re.search(r'(doi:|arxiv:|author|email)', line, re.I):
            title_candidates.append(line)
    
    if title_candidates:
        relevant_sections.append(f"TITLE: {title_candidates[0]}")
    
    # Find abstract section (more flexible matching)
    abstract_start = -1
    abstract_end = -1
    for i, line in enumerate(lines):
        # Look for "Abstract" or "ABSTRACT" as standalone word
        if re.match(r'^\s*abstract\s*$', line, re.IGNORECASE) or \
           re.match(r'^\s*abstract\s*[:.]?\s*$', line, re.IGNORECASE):
            abstract_start = i + 1
            # Look for end of abstract (next section header or significant content break)
            for j in range(i + 1, min(i + 40, len(lines))):
                next_line = lines[j].lower().strip()
                if re.match(r'^(introduction|background|keywords|significance|author|1\.|i\.)', next_line) or \
                   (len(next_line) < 10 and any(word in next_line for word in ['author', 'keyword', 'introduction'])):
                    abstract_end = j
                    break
            break
    
    if abstract_start > 0:
        abstract_end = abstract_end if abstract_end > 0 else min(abstract_start + 25, len(lines))
        abstract_lines = lines[abstract_start:abstract_end]
        # Filter out very short lines that might be formatting artifacts
        abstract_lines = [line for line in abstract_lines if len(line) > 10]
        abstract_text = ' '.join(abstract_lines).strip()
        
        if len(abstract_text) > 100 and is_likely_english(abstract_text):
            relevant_sections.append(f"ABSTRACT: {abstract_text}")
    
    # Find significance/impact statement (common in bioRxiv)
    for i, line in enumerate(lines):
        if re.match(r'^\s*(significance|impact)\s*(statement)?\s*[:.]?\s*$', line, re.IGNORECASE):
            significance_start = i + 1
            significance_lines = lines[significance_start:significance_start + 10]
            significance_text = ' '.join(significance_lines).strip()
            if len(significance_text) > 50 and is_likely_english(significance_text):
                relevant_sections.append(f"SIGNIFICANCE: {significance_text}")
            break
    
    # Find introduction section
    intro_start = -1
    for i, line in enumerate(lines):
        if re.match(r'^\s*(introduction|background|1\.\s*introduction)\s*$', line, re.IGNORECASE):
            intro_start = i + 1
            intro_lines = lines[intro_start:intro_start + 15]
            intro_lines = [line for line in intro_lines if len(line) > 10]
            intro_text = ' '.join(intro_lines).strip()
            if len(intro_text) > 100 and is_likely_english(intro_text):
                relevant_sections.append(f"INTRODUCTION: {intro_text[:500]}...")
            break
    
    # If no structured sections found, use a smarter approach to find content
    if not relevant_sections:
        # Skip the first few lines (likely title/authors) and look for substantial content
        content_start = 0
        for i, line in enumerate(lines[:20]):
            if len(line) > 50 and not re.search(r'(author|email|affiliation|doi:|arxiv:|©)', line, re.I):
                content_start = i
                break
        
        if content_start < len(lines):
            content_lines = lines[content_start:content_start + 20]
            content_lines = [line for line in content_lines if len(line) > 15]
            first_content = ' '.join(content_lines).strip()
            
            if len(first_content) > 100:
                if is_likely_english(first_content):
                    relevant_sections.append(f"CONTENT: {first_content[:800]}...")
                else:
                    relevant_sections.append(f"CONTENT (may be multilingual): {first_content[:800]}...")
    
    result = '\n\n'.join(relevant_sections)
    return result if result else full_text[:1000]  # Fallback to first 1000 chars

def is_likely_english(text: str) -> bool:
    """Simple heuristic to detect if text is likely in English."""
    if not text or len(text) < 20:
        return True   # Too short to determine, assume English
    
    # Count common English words
    english_words = {
        'the', 'and', 'or', 'a', 'an', 'is', 'are', 'was', 'were', 'of', 'in', 'to', 'for',
        'with', 'by', 'this', 'that', 'these', 'those', 'we', 'our', 'study', 'analysis',
        'method', 'results', 'conclusion', 'research', 'data', 'using', 'used', 'based'
    }
    
    words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    if len(words) < 10:
        return True  # Too few words to determine
    
    english_count = sum(1 for word in words if word in english_words)
    english_ratio = english_count / len(words)
    
    # Also check for French/German specific patterns
    french_indicators = ['dans', 'avec', 'pour', 'sur', 'cette', 'leur', 'nous', 'été', 'être']
    german_indicators = ['der', 'die', 'das', 'und', 'oder', 'mit', 'für', 'durch', 'über']
    
    french_count = sum(1 for word in words if word in french_indicators)
    german_count = sum(1 for word in words if word in german_indicators)
    
    # If we detect significant French/German content, it's likely not English
    if french_count > len(words) * 0.05 or german_count > len(words) * 0.05:
        return False
    
    # Consider it English if at least 15% of words are common English words
    return english_ratio > 0.15

def extract_metadata_from_pdf_text(pdf_text: str) -> dict:
    """Find DOI, a plausible title, author line."""
    if not pdf_text:
        return {}
    md = {}
    doi_m = DOI_RE.search(pdf_text)
    if doi_m:
        md["doi"] = doi_m.group(0)
    # crude title guess: first reasonable line before 'Abstract'
    lines = [ln.strip() for ln in pdf_text.splitlines() if ln.strip()]
    title = None
    for ln in lines[:60]:
        if re.match(r"^abstract\b", ln, re.I):
            break
        if 8 <= len(ln) <= 240 and not re.search(r"(doi:|arxiv:)", ln, re.I):
            title = ln
            break
    if title:
        md["title"] = title
    # weak authors pattern
    for j in range(1, 8):
        if j < len(lines):
            cand = lines[j]
            if re.search(r"[A-Z][a-z]+\s+[A-Z][a-z]+", cand):
                md["authors_info"] = cand
                break
    return md

def google_search_fallback(query: str):
    """Very light fallback via Google Custom Search (requires valid key & cx)."""
    try:
        r = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "q": query,
                "key": OPENAI_API_KEY,  # reuse key; replace with your proper CSE key
                "cx": "017576662512468239146:omuauf_lfve",  # demo CX; replace with your own
            },
            timeout=20,
        )
        data = r.json()
        items = data.get("items", []) or []
        if not items:
            return []
        out = []
        for it in items:
            out.append({
                "title": it.get("title"),
                "url": it.get("link"),
                "authors_info": "",
                "snippet": it.get("snippet"),
                "pdf_url": "",
                "doi": None,
                "venue": None,
                "year": None
            })
        return out
    except Exception:
        return []

if search_mode == "Keyword Search":
    # Multi-step query workflow
    ready_to_search, final_query, should_execute = render_query_workflow()
    
    if not ready_to_search:
        st.stop()  # Don't show search results until workflow is complete
elif search_mode == "Paste citation / page text":
    # Multi-step text extraction workflow  
    ready_to_process, edited_refs = render_text_extraction_workflow()
    
    if not ready_to_process:
        st.stop()  # Don't show search results until workflow is complete
    should_execute = ready_to_process
else:
    # URL/DOI mode - execute immediately if URL is provided
    should_execute = bool(url_or_doi and url_or_doi.strip())

# ============================
# MAIN ACTION (Enhanced)
# ============================
# Execute search when the flag is set or for URL/DOI mode
execute_search = False
if search_mode == "Keyword Search" and st.session_state.get('should_execute_search', False):
    execute_search = True
elif search_mode == "Paste citation / page text" and st.session_state.get('should_execute_search', False):
    execute_search = True
elif search_mode == "Lookup by URL / PDF " and url_or_doi and url_or_doi.strip():
    execute_search = True

if execute_search:
    # Clear the flag to prevent repeated execution
    if 'should_execute_search' in st.session_state:
        st.session_state.should_execute_search = False
    progress = st.progress(0)
    status = st.empty()

    papers_meta = []
    try:
        # 1) KEYWORD SEARCH (using final query from workflow)
        if search_mode == "Keyword Search":
            effective_query = final_query
            
            # Validate that we have a non-empty query
            if not effective_query or not effective_query.strip():
                st.error("❌ Cannot execute search with empty query. Please enter search terms.")
                st.stop()
            
            # Apply date filtering if enabled
            search_date_from = date_from if date_filter_enabled else None
            search_date_to = date_to if date_filter_enabled else None
            
            progress.progress(0.10)

            agg = []
            # Get source-specific queries from metadata
            all_queries = st.session_state.query_metadata.get('all_queries', {})
            
            if search_source in ("Semantic Scholar", "Both"):
                status.info("🔎 Searching Semantic Scholar…")
                try:
                    # Use Semantic Scholar optimized query if available
                    semantic_query = all_queries.get("Semantic Scholar", {}).get("boolean_query", effective_query)
                    
                    # Validate semantic query is not empty
                    if not semantic_query or not semantic_query.strip():
                        st.warning("⚠️ Empty Semantic Scholar query - using fallback")
                        semantic_query = effective_query
                        
                    if semantic_query and semantic_query.strip():
                        agg.extend(search_semantic_scholar_with_dates(semantic_query, 
                                                                    limit=max_results,
                                                                    year_from=search_date_from,
                                                                    year_to=search_date_to))
                    else:
                        st.error("❌ No valid query available for Semantic Scholar")
                        st.warning("⚠️ Skipping Semantic Scholar: No valid query available")
                except Exception as e:
                    st.warning(f"Semantic Scholar failed: {e}")
                progress.progress(0.30)

            if search_source in ("PubMed", "Both"):
                status.info("🧬 Searching PubMed…")
                try:
                    # Use PubMed optimized query if available
                    pubmed_query = all_queries.get("PubMed", {}).get("boolean_query", effective_query)
                    
                    # Validate PubMed query is not empty
                    if not pubmed_query or not pubmed_query.strip():
                        pubmed_query = effective_query
                    
                    if pubmed_query and pubmed_query.strip():
                        # Try regular PubMed search first
                        pubmed_results = search_pubmed_with_dates(pubmed_query, 
                                                          limit=max_results,
                                                          year_from=search_date_from,
                                                          year_to=search_date_to)
                        
                        # If no results, try professor's fallback method
                        if not pubmed_results:
                            st.warning("🔄 PubMed API returned 0 results, trying professor's fallback method...")
                            fallback_pmids = search_pubmed_fallback(pubmed_query, max_results)
                            
                            if fallback_pmids:
                                st.success(f"✅ Fallback found {len(fallback_pmids)} papers!")
                                # Convert PMIDs to your paper format
                                for pmid in fallback_pmids:
                                    try:
                                        metadata = fetch_pubmed_metadata(pmid)
                                        if metadata:
                                            pubmed_results.append({
                                                "title": metadata.get("Title", ""),
                                                "url": metadata.get("URL", f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"),
                                                "authors_info": metadata.get("Authors", ""),
                                                "snippet": metadata.get("Abstract", "")[:300] + "..." if metadata.get("Abstract") else "",
                                                "pdf_url": "",
                                                "doi": metadata.get("DOI"),
                                                "venue": metadata.get("Journal", ""),
                                                "year": metadata.get("Year"),
                                                "citationCount": None,
                                                "publicationDate": metadata.get("Year"),
                                                "publicationTypes": metadata.get("PublicationType", []),
                                                "pmid": pmid
                                            })
                                    except Exception as e:
                                        st.warning(f"Could not fetch metadata for PMID {pmid}: {e}")
                                        continue
                            else:
                                st.warning("❌ Both API and fallback methods returned no results")
                        
                        agg.extend(pubmed_results)
                    else:
                        st.warning("⚠️ Skipping PubMed: No valid query available")
                except Exception as e:
                    st.warning(f"PubMed failed: {e}")
                progress.progress(0.50)

            status.info("📦 Combining results…")
            papers_meta = _take(dedupe_results(agg), max_results)
            progress.progress(0.60)

        # 2) PASTE CITATION / TEXT (using edited references from workflow)
        elif search_mode == "Paste citation / page text":
            status.info("🔎 Enriching edited references…")
            collected = []
            
            for i, ref in enumerate(edited_refs):
                title, authors, year, doi = ref.get("title"), ref.get("authors"), ref.get("year"), ref.get("doi")
                enriched = None

                # 1. DOI → Semantic Scholar enrichment
                if doi:
                    enriched = semantic_scholar_by_doi(doi)

                # 2. PubMed by title
                if not enriched and title:
                    pm = search_pubmed(title, 1)
                    enriched = pm[0] if pm else None

                # 3. Google fallback
                if not enriched and title:
                    gg = google_search_fallback(title)
                    enriched = gg[0] if gg else None

                # 4. If still nothing → use edited metadata
                if not enriched:
                    enriched = {
                        "title": title,
                        "authors_info": ", ".join(authors) if isinstance(authors, list) else (authors or ""),
                        "snippet": "",
                        "url": "",
                        "pdf_url": "",
                        "doi": doi,
                        "year": year,
                        "venue": None
                    }
                collected.append(enriched)
                
                # Update progress
                progress.progress(0.30 + (i / len(edited_refs)) * 0.30)

            papers_meta = collected
            progress.progress(0.60)

        # 3) LOOKUP BY URL / DOI / PDF
        else:  # search_mode == "Lookup by URL / PDF"
            if not url_or_doi or not url_or_doi.strip():
                st.warning("Please paste a URL or DOI.")
                st.stop()

            val = url_or_doi.strip()
            status.info("🧭 Resolving input…")
            progress.progress(0.10)

            if DOI_RE.fullmatch(val):
                # DOI path: Crossref enrich + S2 by title if possible
                doi = val
                enr = crossref_enrich(doi)
                title = enr.get("title")
                if title:
                    status.info("🔎 Searching Semantic Scholar by title…")
                    ss = search_semantic_scholar(title, limit=1)
                else:
                    ss = []
                base = {
                    "title": enr.get("title"),
                    "url": enr.get("url"),
                    "authors_info": enr.get("authors_info"),
                    "snippet": "",
                    "pdf_url": "",
                    "doi": doi,
                    "venue": enr.get("venue"),
                    "year": enr.get("year"),
                }
                papers_meta = [ss[0] | base] if ss else [base]
                progress.progress(0.60)
            else:
                # Assume URL - First try to extract DOI from any URL
                extracted_doi = extract_doi_from_url(val)
                
                if extracted_doi:
                    status.info(f"🔍 DOI detected in URL — using universal DOI lookup for better accuracy…")
                    st.info(f"📋 Extracted DOI: {extracted_doi}")
                    
                    doi_data = universal_doi_lookup(extracted_doi)
                    
                    if doi_data and doi_data.get("title"):
                        st.info(f"✅ DOI lookup successful - got rich metadata")
                        # Debug: Show what data we got
                        st.info(f"🔍 Title: {doi_data.get('title', 'NO TITLE')[:100]}...")
                        st.info(f"🔍 Authors: {doi_data.get('authors_info', 'NO AUTHORS')[:100]}...")
                        if doi_data.get('abstract'):
                            st.info(f"🔍 Abstract: {doi_data.get('abstract', '')[:100]}...")
                        else:
                            st.info(f"🔍 No abstract available from publisher")
                        
                        # Use DOI lookup data directly
                        papers_meta = [doi_data]
                        progress.progress(0.70)
                    else:
                        st.warning("⚠️ DOI lookup failed, falling back to PDF parsing")
                        # Fall back to PDF parsing
                        is_pdf, pdf_text = fetch_url_and_guess_pdf(val)
                        progress.progress(0.25)
                        if is_pdf:
                            status.info("📄 PDF detected — extracting metadata…")
                            md = extract_metadata_from_pdf_text(pdf_text)
                            doi = md.get("doi") or extracted_doi
                            enr = crossref_enrich(doi) if doi else {}
                            title = md.get("title") or enr.get("title")
                            
                            base = {
                                "title": title,
                                "url": val,
                                "authors_info": md.get("authors_info") or enr.get("authors_info"),
                                "snippet": clean_snippet(pdf_text[:1200]),
                                "pdf_url": val,
                                "doi": doi,
                                "venue": enr.get("venue"),
                                "year": enr.get("year"),
                            }
                            papers_meta = [base]
                            progress.progress(0.70)
                        else:
                            st.error("Failed to process URL")
                            papers_meta = []
                else:
                    # No DOI found - try traditional PDF/URL processing
                    is_pdf, pdf_text = fetch_url_and_guess_pdf(val)
                    progress.progress(0.25)
                    if is_pdf:
                        status.info("📄 PDF detected — extracting metadata…")
                        
                        md = extract_metadata_from_pdf_text(pdf_text)
                        doi = md.get("doi")
                        if doi:
                            # Found DOI in PDF, use universal lookup
                            st.info(f"📋 Found DOI in PDF: {doi}")
                            doi_data = universal_doi_lookup(doi)
                            if doi_data and doi_data.get("title"):
                                papers_meta = [doi_data]
                                progress.progress(0.70)
                            else:
                                # Fall back to basic PDF metadata
                                enr = crossref_enrich(doi)
                                title = md.get("title") or enr.get("title")
                                base = {
                                    "title": title,
                                    "url": val,
                                    "authors_info": md.get("authors_info") or enr.get("authors_info"),
                                    "snippet": clean_snippet(pdf_text[:1200]),
                                    "pdf_url": val,
                                    "doi": doi,
                                    "venue": enr.get("venue"),
                                    "year": enr.get("year"),
                                }
                                papers_meta = [base]
                                progress.progress(0.70)
                        else:
                            # No DOI found in PDF
                            title = md.get("title")
                            
                            # Debug: Show extracted content quality
                            if pdf_text:
                                content_preview = pdf_text[:200].replace('\n', ' ')
                                st.info(f"📝 Extracted content preview: {content_preview}...")
                            
                            status.info("🔎 Searching Semantic Scholar by title…")
                            ss = search_semantic_scholar(title, limit=1) if title else []
                            base = {
                                "title": title,
                                "url": val,
                                "authors_info": md.get("authors_info"),
                                "snippet": clean_snippet(pdf_text[:1200]),
                                "pdf_url": val,
                                "doi": None,
                                "venue": None,
                                "year": None,
                            }
                            papers_meta = [ss[0] | base] if ss else [base]
                            progress.progress(0.70)
                    else:
                        status.info("🌐 Not a PDF — trying title guess from URL path…")
                        guessed = re.sub(r"[-_/]+", " ", val.split("//")[-1])[:120]
                        ss = search_semantic_scholar(guessed, limit=1)
                        papers_meta = ss
                        progress.progress(0.70)

        # Initialize Zotero (optional)
        zot = None  # Initialize to None first
        if add_to_zotero and user_zotero_key and user_zotero_id:
            try:
                zot = zotero.Zotero(user_zotero_id, 'user', user_zotero_key)
            except Exception as e:
                st.error(f"Zotero initialization error: {e}")
                zot = None  # Ensure zot is None if initialization fails

        # If nothing found — friendly message
        if not papers_meta:
            status.warning("")
            progress.progress(1.0)
            st.error("😅 We searched high, low, and even peered behind the paywall sofa cushions… but found nada.")
            st.caption("Try tweaking the query or switching modes. Even librarians have off days.")
            st.stop()

        # Render + Gemini analysis (UNIFIED)
        status.info("🧪 Analyzing and annotating…")
        progress.progress(0.75)

        # Map Zotero threshold: score3 (0..3)
        zotero_threshold_score3 = min(3, max(0, int(min_score3)))

        for i, paper in enumerate(papers_meta):
            title = paper.get("title", "")
            url = paper.get("url", "")
            authors_info = paper.get("authors_info", "")
            snippet = paper.get("snippet", "")
            pdf_url = paper.get("pdf_url", "")
            doi = paper.get("doi")
            venue = paper.get("venue")
            year = paper.get("year")

            # Store comprehensive paper metadata in session state for Zotero enhancement
            comprehensive_metadata = {
                'citationCount': paper.get('citationCount'),
                'publicationDate': paper.get('publicationDate'), 
                'publicationTypes': paper.get('publicationTypes'),
                'venue': venue,
                'year': year,
                'source_data': paper  # Store the complete paper object
            }
            st.session_state.current_paper_metadata = comprehensive_metadata

            # Pull PDF text when useful
            pdf_text = extract_pdf_text(pdf_url or url)

            with st.expander(f"📄 {title or 'Untitled'}", expanded=True):
                if authors_info:
                    st.markdown(f"**Authors:** {authors_info}")
                if venue or year:
                    st.markdown(f"**Venue / Year:** {venue or '—'} — {year or '—'}")
                if snippet:
                    st.markdown(f"**Abstract (source):** {snippet}")

                # Enhanced AI annotation with professor's gpt-5-mini rating system
                
                # Determine user query context  
                if search_mode == 'Keyword Search':
                    user_query = st.session_state.query_metadata.get('original_input', '')
                elif search_mode == 'Paste citation / page text':
                    user_query = title or "extracted reference"
                else:
                    user_query = url_or_doi
                
                try:
                    # Get user's research area classification for better rating
                    query_classification = st.session_state.get('query_classification', 1)  # Default to general
                    
                    # Use professor's rating system if we have the classification
                    if query_classification and hasattr(st.session_state, 'query_classification'):
                        # Removed: st.info("🧠 Using Professor's Enhanced gpt-5-mini Rating System")
                        
                        # Create metadata dict in professor's format
                        prof_metadata = {
                            "Title": title,
                            "Authors": authors_info,
                            "Journal": venue or "Unknown",
                            "Year": str(year) if year else "Unknown",
                            "Abstract": snippet or pdf_text[:500] if pdf_text else "",
                            "DOI": doi or ""
                        }
                        
                        # Get professor's rating
                        rating_text = rate_publication(prof_metadata, query_classification)
                        score_prof, keywords_prof, note_prof = parse_gpt4_output(rating_text)
                        
                        # Generate institutional links
                        institutional_links = remotexs_links(doi) if doi else []
                        
                        # Generate institutional links (display them here)
                        if institutional_links:
                            st.markdown("**🏫 Institutional Access Links:**")
                            for description, link in institutional_links:
                                st.markdown(f"[{description}]({link})")
                        # Use professor's results as primary, with fallback to your system
                        tags = keywords_prof if keywords_prof else []
                        score3 = score_prof
                        abstract_ai = note_prof
                        
                    else:
                        # Fallback to your existing OpenAI system
                        abstract_ai, tags, score3 = openai_annotate_paper(
                            title, authors_info, snippet, pdf_text, url, user_query
                        ) if OPENAI_API_KEY else ("", [], 0)
                        
                except Exception as e:
                    st.error(f"Enhanced AI rating error: {e}")
                    # Fallback to your existing system
                    try:
                        abstract_ai, tags, score3 = openai_annotate_paper(
                            title, authors_info, snippet, pdf_text, url, user_query
                        ) if OPENAI_API_KEY else ("", [], 0)
                    except Exception as e2:
                        st.error(f"OpenAI API error: {e2}")
                        abstract_ai, tags, score3 = "", [], 0

                # Only display tags, abstract, and AI relevance once (after fallback/primary logic)
                if abstract_ai:
                    st.markdown("**Abstract (AI):**")
                    st.write(abstract_ai)
                if tags:
                    display_tags = normalize_tags(tags)
                    st.markdown("**🏷️ Tags:** " + ", ".join(display_tags))
                st.markdown(f"**AI Relevance (0-3):** `{score3}`")

                # Zotero save with enhanced metadata extraction from all sources  
                # Allow saving to default library (root) when user_zotero_collection is empty
                if add_to_zotero and zot and (score3 >= zotero_threshold_score3):
                    # Normalize tags to ensure consistent format across all modes
                    normalized_tags = normalize_tags(tags or [])
                    
                    # Smart tag processing to reduce redundancy
                    processed_tags, tag_suggestions = smart_tag_processing(normalized_tags, zot)
                    
                    # Collect comprehensive metadata from all available sources
                    doi_or_url = f"https://doi.org/{doi}" if doi else url
                    proxy_url = with_ntu_proxy(doi_or_url, style=1) or with_ntu_proxy(doi_or_url, style=2) or url
                    
                    # Get enhanced metadata from Crossref if DOI available
                    crossref_data = crossref_enrich(doi) if doi else {}
                    
                    # Get bioRxiv data if applicable
                    biorxiv_data = {}
                    if doi and doi.startswith("10.1101/"):
                        biorxiv_data = biorxiv_api_fetch(doi)
                    
                    # Prepare semantic scholar data from current metadata
                    semantic_scholar_data = {}
                    if hasattr(st.session_state, 'current_paper_metadata'):
                        # This would be set during search processing if available
                        semantic_scholar_data = getattr(st.session_state, 'current_paper_metadata', {})
                    
                    # Create comprehensive Zotero item
                    item = create_enhanced_zotero_item(
                        title=title,
                        authors_info=authors_info,
                        abstract=abstract_ai,
                        snippet=snippet,
                        url=url,
                        doi=doi,
                        year=year,
                        venue=venue,
                        crossref_data=crossref_data,
                        biorxiv_data=biorxiv_data,
                        semantic_scholar_data=semantic_scholar_data,
                        tags=processed_tags,
                        collection_id=user_zotero_collection,
                        proxy_url=proxy_url
                    )

                    duplicate_found = False
                    if not allow_duplicates and title.strip() and zot:
                        try:
                            existing_items = zot.items(q=title, itemType="journalArticle")
                            for existing in existing_items:
                                t = existing.get("data", {}).get("title", "").strip().lower()
                                if t == title.strip().lower():
                                    duplicate_found = True
                                    break
                            if doi and not duplicate_found:
                                existing2 = zot.items(q=doi, itemType="journalArticle")
                                for ex in existing2:
                                    if doi and (doi.lower() in json.dumps(ex.get("data", {})).lower()):
                                        duplicate_found = True
                                        break
                        except Exception as e:
                            st.warning(f"⚠️ Zotero duplicate check failed: {e}")

                    if duplicate_found and not allow_duplicates:
                        st.warning(f"⚠️ Skipped Zotero save: duplicate found for '{title}'")
                    else:
                        try:
                            result = zot.create_items([item])
                            if processed_tags != tags:
                                st.success(f"✅ Added to Zotero with optimized tags (ai_score={score3})")
                            else:
                                st.success(f"✅ Added to Zotero (ai_score={score3})")
                        except Exception as e:
                            st.error(f"❌ Zotero error: {e}")
                # If not saving to Zotero, no need to show debug reasons

        status.success("Done ✅")
        progress.progress(1.0)

    finally:
        # Clear status after a short delay to avoid lingering messages
        sleep(0.4)
        status.empty()

def render_admin_panel():
    st.sidebar.markdown("---")
    st.sidebar.markdown("**🛡️ Admin Panel**")
    users = load_users()
    # Set group Zotero API credentials
    st.sidebar.markdown("**Group Zotero API Settings**")
    group_zotero_key = st.sidebar.text_input("Group Zotero API Key", value=st.session_state.get("group_zotero_key", ""), type="password")
    group_zotero_id = st.sidebar.text_input("Group Zotero User ID", value=st.session_state.get("group_zotero_id", ""))
    group_zotero_collection = st.sidebar.text_input("Group Zotero Collection ID", value=st.session_state.get("group_zotero_collection", ""))
    if st.sidebar.button("Save Group Zotero Settings"):
        st.session_state.group_zotero_key = group_zotero_key
        st.session_state.group_zotero_id = group_zotero_id
        st.session_state.group_zotero_collection = group_zotero_collection
        st.sidebar.success("Group Zotero settings saved for this session.")
    # Set default interests
    st.sidebar.markdown("**Set Default Interests for All Users**")
    default_topics = st.sidebar.text_input("Default Topics (comma-separated)", value=", ".join(st.session_state.get("default_topics", [])))
    default_authors = st.sidebar.text_input("Default Authors (comma-separated)", value=", ".join(st.session_state.get("default_authors", [])))
    default_journals = st.sidebar.text_input("Default Journals (comma-separated)", value=", ".join(st.session_state.get("default_journals", [])))
    if st.sidebar.button("Apply Defaults to All Users"):
        topics = [t.strip() for t in default_topics.split(",") if t.strip()]
        authors = [a.strip() for a in default_authors.split(",") if a.strip()]
        journals = [j.strip() for j in default_journals.split(",") if j.strip()]
        for uname, udata in users.items():
            udata["profile"]["topics"] = topics
            udata["profile"]["authors"] = authors
            udata["profile"]["journals"] = journals
        save_users(users)
        st.session_state.default_topics = topics
        st.session_state.default_authors = authors
        st.session_state.default_journals = journals
        st.sidebar.success("Defaults applied to all users.")
    # Delete users
    st.sidebar.markdown("**Delete User**")
    del_user = st.sidebar.selectbox("Select user to delete", [u for u in users if u != st.session_state.username])
    if st.sidebar.button("Delete Selected User"):
        if del_user in users:
            del users[del_user]
            save_users(users)
            st.sidebar.success(f"User '{del_user}' deleted.")

def is_admin():
    users = load_users()
    return users.get(st.session_state.username, {}).get("is_admin", False)

# ============================
# PROFESSOR'S ALIGNED FUNCTIONS
# ============================

def what_is_requested(text_list):
    """
    Classify input text using gpt-5-mini to determine search intent.
    Returns: (classification_int, classification_text)
    1 = topic/keyword search, 2 = evolution research, 3 = chemistry research, 4 = PMIDs
    """
    try:
        text = text_list[0] if isinstance(text_list, list) else str(text_list)
        
        # Check for PMIDs first
        pmid_pattern = r'\b\d{8,}\b'
        pmids_found = re.findall(pmid_pattern, text)
        if len(pmids_found) >= 2:  # Multiple PMIDs found
            return [4], ["PMID list"]
            
        prompt = f"""
        Analyze this research query and classify it into one of these categories:
        1. General topic/keyword search
        2. Evolution/evolutionary biology research  
        3. Physical chemistry/biochemistry research
        4. PMID numbers (8+ digit numbers)
        
        Text: "{text}"
        
        Respond with just the number (1-4) and a brief classification.
        Format: "Classification: X - Description"
        """
        
        if not openai_client:
            # Fallback if no OpenAI client available
            return [1], ["General topic search"]
            
        response = openai_client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        
        result_text = response.choices[0].message.content.strip()
        
        # Parse response
        if "Classification: 1" in result_text:
            return [1], ["General topic search"]
        elif "Classification: 2" in result_text:
            return [2], ["Evolution research"]
        elif "Classification: 3" in result_text:
            return [3], ["Physical chemistry research"]
        elif "Classification: 4" in result_text:
            return [4], ["PMID list"]
        else:
            return [1], ["General topic search"]  # Default fallback
            
    except Exception as e:
        st.warning(f"Classification error: {e}")
        return [1], ["General topic search"]

def get_zotero_group_for_research_area(research_area):
    """
    Map research area to specific Zotero group/collection IDs
    Based on professor's group assignment logic
    """
    # Professor's group mappings (you can customize these)
    group_mappings = {
        "physchem": {
            "group_id": "6059722",  # chemistry group  
            "recent_group_id": "6072114"  # recently_added_with_ai_annotation
        },
        "evolution": {
            "group_id": "6064631",  # evolution group
            "recent_group_id": "6073428"  # K.P. Evolution recently added
        }
    }
    
    area_lower = research_area.lower()
    if "physchem" in area_lower or "chemistry" in area_lower:
        return group_mappings["physchem"]
    elif "evolution" in area_lower:
        return group_mappings["evolution"] 
    else:
        # Default to chemistry groups
        return group_mappings["physchem"]

def add_pubmed_reference_to_zotero(research_area, pmid, keywords, notes_list, note_types, institutional_links):
    """
    Add PubMed reference to Zotero with enhanced metadata and institutional links
    Based on professor's Zotero integration pattern with group-specific collections
    """
    try:
        # Get user's Zotero credentials
        profile = get_user_profile(st.session_state.username)
        if not profile.get("zotero_user_id") or not profile.get("zotero_api_key"):
            st.error("❌ Zotero credentials not configured. Please set them in your profile.")
            return False
        
        # Get group mapping for research area
        group_info = get_zotero_group_for_research_area(research_area)
        target_group_id = group_info["recent_group_id"]  # Use recent group for new additions
        
        # Use group credentials if available, otherwise fall back to user credentials
        group_key = st.session_state.get("group_zotero_key", "")
        group_id = st.session_state.get("group_zotero_id", "")
        
        if group_key and group_id:
            zot = zotero.Zotero(group_id, 'group', group_key)
            st.info(f"📚 Using group Zotero (ID: {group_id}) for {research_area}")
        else:
            zot = zotero.Zotero(profile["zotero_user_id"], 'user', profile["zotero_api_key"])
            st.info(f"📚 Using personal Zotero for {research_area}")
        
        # Fetch PubMed metadata
        metadata = fetch_pubmed_metadata(pmid)
        if not metadata:
            st.error(f"❌ Could not fetch metadata for PMID: {pmid}")
            return False
        
        # Create enhanced Zotero item
        zotero_item = create_enhanced_zotero_item(metadata, keywords, institutional_links)
        
        # Add notes with research area and group info
        combined_notes = " | ".join(notes_list) if notes_list else ""
        area_note = f"Research Area: {research_area} | Target Group: {target_group_id}"
        if combined_notes:
            combined_notes = f"{area_note} | {combined_notes}"
        else:
            combined_notes = area_note
            
        zotero_item["notes"] = [{"note": f"<p>{combined_notes}</p>"}]
        
        # Add specific collection if available
        collection_id = st.session_state.get("group_zotero_collection", "")
        if collection_id:
            zotero_item["collections"] = [collection_id]
            st.info(f"📁 Adding to collection: {collection_id}")
        
        # Create item in Zotero
        response = zot.create_items([zotero_item])
        
        if response.get('successful'):
            group_type = "group" if (group_key and group_id) else "personal"
            st.success(f"✅ Added PMID {pmid} to {group_type} Zotero successfully")
            return True
        else:
            st.error(f"❌ Failed to add to Zotero: {response}")
            return False
            
    except Exception as e:
        st.error(f"❌ Zotero integration error: {e}")
        return False

def check_zotero_ref_via_search(title, group_id):
    """
    Check if reference already exists in Zotero by searching title
    """
    try:
        profile = get_user_profile(st.session_state.username)
        if not profile.get("zotero_user_id") or not profile.get("zotero_api_key"):
            return False
            
        zot = zotero.Zotero(profile["zotero_user_id"], 'user', profile["zotero_api_key"])
        
        # Search for title in Zotero
        results = zot.everything(zot.items(q=title[:50]))  # Search first 50 chars of title
        
        return len(results) > 0
        
    except Exception:
        return False  # If search fails, assume not present





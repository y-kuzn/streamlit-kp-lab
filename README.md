# Lab Lit App

A Streamlit app for literature review, AI grading, and Zotero integration.

pip install -r requirements.txt

## Deployment

### Local
1. Copy `.env.example` to `.env` and fill in your API keys and settings.
2. Copy `users.json.example` to `users.json` and edit users as needed.
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Run the app locally:
   ```
   streamlit run lab_lit_app.py
   ```

### Azure App Service (with Docker)
1. Copy `.env.example` to `.env` and fill in your API keys and settings.
2. Copy `users.json.example` to `users.json` and edit users as needed.
3. Build and push your Docker image:
   ```
   docker build -t <your-dockerhub-username>/lab-lit-app .
   docker push <your-dockerhub-username>/lab-lit-app
   ```
4. In Azure Portal, create a Web App for Containers and set the image source to your pushed Docker image.
5. Set environment variables in Azure App Service to match your `.env` file (do not upload `.env` with secrets).

### Azure App Service (without Docker)
1. Copy `.env.example` to `.env` and fill in your API keys and settings.
2. Copy `users.json.example` to `users.json` and edit users as needed.
3. Deploy all files to Azure App Service.
4. Set the startup command to:
   ```
   ./startup.sh
   ```
5. Set environment variables in Azure App Service to match your `.env` file.


## Files
- `lab_lit_app.py`: Main app logic
- `.env.example`: Environment variable template
- `users.json.example`: User config template
- `requirements.txt`: Python dependencies
- `startup.sh`: Startup script for Azure
- `Dockerfile`: Container build file for Docker/Azure

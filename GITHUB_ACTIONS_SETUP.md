# GitHub Actions Setup Guide

This guide explains how to set up and use the GitHub Actions workflows in your Home Credit Default Risk project.

## Overview

Two workflows have been created:

1. **ci.yml** - Continuous Integration (runs on every push and pull request)
2. **docker-push.yml** - Docker Image Build and Push (runs on main branch and tags)

---

## Step 1: Push Your Code to GitHub

First, ensure your project is on GitHub:

```bash
git init
git add .
git commit -m "Initial commit with GitHub Actions"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/Home-Credit-Default-Risk.git
git push -u origin main
```

---

## Step 2: Verify the Workflows Are Active

1. Go to your GitHub repository
2. Click on the **Actions** tab
3. You should see:
   - `CI Pipeline` workflow
   - `Build and Push Docker Image` workflow

Both should be visible in the Workflows list on the left sidebar.

---

## Workflow 1: CI Pipeline (`ci.yml`)

**Triggers:** Every push to `main` or `develop`, and on pull requests

**What it does:**

### 1. **Lint Job** - Code Quality Checks
   - Sets up Python 3.11
   - Installs linting tools (flake8, pylint)
   - Checks for syntax errors and code style issues
   - ✅ Does NOT fail the build (soft warnings)

### 2. **Test Job** - Application Testing
   - Sets up Python environment
   - Installs all project dependencies
   - Tests that modules import correctly
   - Verifies data cleaning module works
   - ✅ Required to pass before Docker build

### 3. **Docker Job** - Docker Image Build
   - Builds the Docker image
   - Uses GitHub Actions cache for faster builds
   - ✅ Only runs if lint and test jobs pass

**How to monitor:**
- Go to **Actions** tab → Click on a workflow run
- View logs for each job
- Failed tests will show error details

**To fix lint issues:**
```bash
pip install flake8 pylint
flake8 Backend/
pylint Backend/main.py Backend/app.py Backend/Cleaning.py
```

---

## Workflow 2: Docker Push (`docker-push.yml`)

**Triggers:** 
- Push to `main` branch
- Git tags starting with `v` (e.g., `v1.0.0`)
- Manual trigger via **Actions** tab

**What it does:**

1. **Logs into GitHub Container Registry (GHCR)**
   - Uses built-in `GITHUB_TOKEN` (automatic)
   
2. **Builds Docker image** with tags:
   - `latest` (for main branch)
   - Version tags (for git tags like `v1.0.0`)
   - Commit SHA for traceability

3. **Pushes image** to `ghcr.io/your-username/home-credit-default-risk`

**Access your Docker image:**
```bash
docker pull ghcr.io/YOUR_USERNAME/Home-Credit-Default-Risk:latest
docker run -p 7860:7860 -p 8000:8000 ghcr.io/YOUR_USERNAME/Home-Credit-Default-Risk:latest
```

---

## Step 3: (Optional) Set Up Secrets for Private Registries

If you want to push to **Docker Hub** instead of GitHub Container Registry:

1. Go to **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Add secrets:
   - Name: `DOCKERHUB_USERNAME` | Value: your Docker Hub username
   - Name: `DOCKERHUB_TOKEN` | Value: your Docker Hub personal access token

Then modify `docker-push.yml` to use Docker Hub (see advanced section below).

---

## Step 4: Create Release Tags

To trigger the Docker workflow with version tags:

```bash
git tag -a v1.0.0 -m "Release version 1.0.0"
git push origin v1.0.0
```

The workflow will:
- Build the image
- Tag it with `v1.0.0`, `1.0`, and `latest`
- Push to GitHub Container Registry

---

## Common Tasks

### View Workflow Results
1. Go to **Actions** tab
2. Click on the workflow run
3. Click on the job to see detailed logs
4. Green checkmark ✅ = success
5. Red X ❌ = failure (click to see error details)

### Re-run a Failed Workflow
1. Click on the failed workflow run
2. Click **Re-run jobs** → **Re-run failed jobs**

### Skip Workflow on Specific Commits
Add to commit message:
```
git commit -m "Update README [skip ci]"
```

### Manual Workflow Trigger
Go to **Actions** → Select workflow → Click **Run workflow** button

---

## Troubleshooting

### Issue: Workflow fails with import errors
**Solution:** Ensure `requirements.txt` includes all dependencies
```bash
pip freeze > requirements.txt
git add requirements.txt
git commit -m "Update dependencies"
git push
```

### Issue: Docker build fails
**Solution:** Test locally first
```bash
docker build -t test .
docker run -p 7860:7860 -p 8000:8000 test
```

### Issue: Docker push fails with authentication
**Solution:** GitHub automatically provides `GITHUB_TOKEN`. Ensure:
1. Workflow has correct permissions:
   ```yaml
   permissions:
     packages: write
   ```
2. Repository is public OR you have admin access

### Issue: Workflows not running
**Solution:** Check if Actions are enabled
1. Go to **Settings** → **Actions** → **General**
2. Ensure "Actions permissions" is set to **Allow all actions**

---

## Advanced Customization

### Push to Docker Hub instead of GHCR

Modify `.github/workflows/docker-push.yml`:

```yaml
env:
  REGISTRY: docker.io
  IMAGE_NAME: ${{ secrets.DOCKERHUB_USERNAME }}/home-credit-default-risk

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}
      # ... rest of the workflow
```

### Add Tests for Backend API

Add a `tests/` folder and update `ci.yml`:

```yaml
- name: Run unit tests
  run: |
    pytest tests/ --cov=Backend --cov-report=xml

- name: Upload coverage to Codecov
  uses: codecov/codecov-action@v3
  with:
    file: ./coverage.xml
```

### Add Security Scanning

Add to `ci.yml`:

```yaml
- name: Run Bandit (security check)
  run: |
    pip install bandit
    bandit -r Backend/ -f json -o bandit.json || true
```

---

## File Structure

After setup, your project should have:

```
.github/
├── workflows/
│   ├── ci.yml              # Main CI/CD pipeline
│   └── docker-push.yml     # Docker build and push
Home-Credit-Default-Risk/
├── Backend/
├── Data/
├── data cleaning/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## Next Steps

1. ✅ Commit and push the `.github/workflows/` folder
2. ✅ Go to **Actions** tab and verify workflows run
3. ✅ Fix any linting or import issues shown in logs
4. ✅ Create a release tag (e.g., `v1.0.0`) to trigger Docker push
5. ✅ Pull your Docker image from GitHub Container Registry

---

## Support

For more information:
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Docker Build Action](https://github.com/docker/build-push-action)
- [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)

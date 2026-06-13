# Minder Onboarding Web App — Design Note

## Goal

Single web interface that takes a fresh Minder box from unboxing to working in under 10 minutes. No terminal, no `.env` files, no separate dashboards.

## Onboarding Flow

### Step 1: Welcome + Network
- Auto-detect local IP and subnet
- Show network info to user
- "Let's find your cameras and devices"

### Step 2: Camera Discovery
- Run ONVIF scan automatically
- Show discovered cameras with live snapshots
- User names each camera (suggest OSD names as defaults)
- User provides camera credentials if needed (try common defaults first)

### Step 3: Smart Device Setup
- Start HA container in background
- Create HA account programmatically (or with minimal user input)
- Show SmartLife/Tuya OAuth login (embedded iframe or redirect)
- User logs in with their existing SmartLife credentials
- HA discovers devices automatically
- Show discovered devices, user confirms

### Step 4: Telegram Bot Setup
- Show step-by-step BotFather instructions with screenshots
- Or: provide a "Create Bot" button that opens BotFather deep link
- User pastes the token
- App verifies the token works
- Show QR code to open the bot in Telegram

### Step 4b: Authorized Users
- "Who can access Minder?"
- User adds family members by Telegram username or phone number
- Owner is auto-added as admin
- Roles:
  - **Admin**: full access — device control, camera access, rule management, add/remove users
  - **Member**: camera snapshots, status queries, receive alerts
  - **Alert-only**: just receives monitoring alerts, no interaction
- Stored in `/data/authorized_users.json`
- Bot rejects messages from unauthorized users with: "This Minder instance is private. Ask the owner to add you."
- Users can be added/removed via Telegram: "/adduser @username member" (admin only)

### Step 5: AI Model Setup
- Detect available hardware (CPU/GPU/RAM)
- Recommend model configuration:
  - GPU available: local qwen3.5:9b + gemma4:e2b
  - CPU only: cloud reasoning (OpenRouter) + local vision
  - User provides OpenRouter key if needed
- Pull/verify Ollama models

### Step 6: Monitoring Rules
- "What should Minder watch for?"
- Template rules: person at gate after dark, car missing, animal detected
- User selects and customizes
- Creates trigger configs

### Step 7: Done
- Summary of everything configured
- "Send /start to your Telegram bot to begin"
- Show system status: cameras online, devices connected, models loaded

## Tech Stack

- **Backend**: Python (FastAPI) — runs in the same Docker container as SwarmKit
- **Frontend**: Simple HTML/JS (no framework needed for a wizard)
- **Port**: 80 or 3000 — the first thing user sees when they open the box's IP
- **State**: writes to `/data/minder-config.json` which generates `.env`, workspace YAML, HA config

## Integration Points

- Camera MCP server's `discover_cameras()` and `capture_camera_snapshot()`
- HA REST API for device setup + token generation
- Telegram Bot API for token verification
- Ollama API for model management
- SwarmKit workspace YAML generation

## Key Principle

The onboarding app replaces ALL manual configuration:
- No `.env` file editing
- No docker-compose changes
- No HA dashboard access
- No BotFather instructions beyond "paste the token"

Everything the user needs is in one browser tab at `http://minder.local`.

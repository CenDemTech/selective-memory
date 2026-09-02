# Conversation filter for chat data portability between platforms

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/downloads/)
[![Status](https://img.shields.io/badge/Status-Active-success)](https://github.com/CenDemTech/chat-data-portability)

Open-source prototype to enable users to upload their conversation data from major LLM platforms, redact potentially sensitive information from conversations and choose which conversations they want to port from one system to another, and download a new copy of their conversation data for upload.

## Quick Install

1. **Create a virtual environment** (one-time setup):
   ```bash
   cd data-portability-app
   python3 -m venv .venv
   ./.venv/bin/pip install -r requirements.txt
   ```

2. **Start the development server**:
   ```bash
   ./.venv/bin/python app.py
   ```

3. **Open in your browser**:
   Navigate to [http://127.0.0.1:5000](http://127.0.0.1:5000)

## How to Use

1. **Select Platform** — Choose which platform service your conversation export is from (Claude or ChatGPT/OpenAI)
2. **Upload File** — Drag and drop or browse for your `conversations.json` file
3. **Review Conversations** — View all conversations in a searchable, sortable table with metadata (date, message count, word count, sensitivity tag)
   - Review conversations
   - Take action on conversations with possibly sensitive content which is highlighted by category (Red, Yellow, or Green tags)
   - Redact detected sensitive content
   - Redact any additional content that is deemed sensitive upon review
   - Delete messages
   - Delete conversations
4. **Select for export** — Review the conversations you want to keep.
6. **Download** — Save your filtered `conversations.json` file for upload to another platform.

The app runs entirely locally—your data never leaves your machine.

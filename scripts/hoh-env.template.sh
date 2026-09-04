# Copy this file outside git or to hoh-env.local.sh, then replace template values.
# Do not commit real Telegram tokens, chat IDs, user IDs, or provider API keys.
# Load it with: . ./hoh-env.local.sh

export HOH_TELEGRAM_BOT_TOKEN="<bot-token>"
export HOH_TELEGRAM_CHAT_ID="<operator-chat-id>"
export HOH_TELEGRAM_USER_ID="<allowed-operator-user-id>"

# Optional provider keys for external worker/supervisor integrations.
# export OPENAI_API_KEY="<openai-api-key>"
# export ANTHROPIC_API_KEY="<anthropic-api-key>"

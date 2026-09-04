# Copy this file outside git or to hoh-env.local.ps1, then replace template values.
# Do not commit real Telegram tokens, chat IDs, user IDs, or provider API keys.

$env:HOH_TELEGRAM_BOT_TOKEN = "<bot-token>"
$env:HOH_TELEGRAM_CHAT_ID = "<operator-chat-id>"
$env:HOH_TELEGRAM_USER_ID = "<allowed-operator-user-id>"

# Optional provider keys for external worker/supervisor integrations.
# $env:OPENAI_API_KEY = "<openai-api-key>"
# $env:ANTHROPIC_API_KEY = "<anthropic-api-key>"

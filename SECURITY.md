# Security / Безопасность / Безпека

This is an early preview. Use the newest prerelease; older previews do not have a
separate maintenance branch. No security audit or signing certificate is claimed.

Please use GitHub private vulnerability reporting when available on this repository.
If that option is unavailable, open an issue requesting a private contact without
posting exploit details, credentials, private logs or machine identifiers.

Не публикуйте ключи, личные настройки и необезличенные отчёты. Для уязвимостей
используйте приватное сообщение GitHub, если оно доступно, или запросите закрытый
канал связи в issue без технических подробностей уязвимости.

Не публікуйте ключі, приватні налаштування та незнеособлені звіти. Для вразливостей
використовуйте приватне повідомлення GitHub, якщо доступне, або попросіть закритий
канал зв'язку в issue без технічних подробиць вразливості.

The GUI runs as the current user. Windows startup is opt-in. Model and agent
processes are stopped only after ownership checks. The optional GPU helper has a
narrow named-pipe API; its installed-service validation is still pending.

# Security

## Reporting

Use [private vulnerability reporting](https://github.com/Revg1794/YT-Music-Karaoke/security/advisories/new)
rather than a public issue. This is a hobby project maintained by one person, so
expect a reply in days, not hours.

## Never share your `browser.json`

That file is a signed-in session for your Google account, not just for YouTube
Music. Anyone who has it can act as you. It is gitignored for that reason.

Don't paste it into an issue, a Discussion, a forum, or a chat with someone
helping you debug — and the same goes for anything you copied out of DevTools
while setting up auth: cookie tables, `Copy as cURL` output, raw request headers.

**If you've already shared it:** sign out of Google on all devices
(Google Account → Security → Your devices), which invalidates the cookies, then
run `setup_auth.py` again to make a fresh session.

## What's worth reporting

- Anything that could expose `browser.json` or its contents.
- A way to reach the backend from somewhere it shouldn't be reachable.
- Party mode is designed to be opened by guests on your network, so anything a
  guest can do beyond queueing songs — reading your library in unintended ways,
  affecting the host machine — is worth flagging.

## Known and accepted

- **Party mode has no authentication by design.** Anyone who can reach the URL on
  your network can queue songs. That's the point of it at a party; don't expose
  it to the internet.
- **`ytmusicapi` is unofficial.** YouTube can change or break it at any time.

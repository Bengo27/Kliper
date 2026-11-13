# Clip Genie licence server: setup (one time, ~20 minutes)

This small server does "Sign in with Twitch" and counts the free trial (3 streams/clips per Twitch account
and per PC). It runs on Cloudflare's free plan. Only the compiled public/tester build uses it; your own
Clip Genie (running from source) never asks you to sign in.

## 1. Cloudflare (free)
1. Make a free account at https://dash.cloudflare.com/sign-up
2. In a terminal in this folder (`server\licence`):
   ```
   npm install
   npx wrangler login
   npx wrangler d1 create clipgenie-licence
   ```
   Copy the `database_id` it prints into `wrangler.toml` (replace `REPLACE_WITH_ID_FROM_wrangler_d1_create`).
3. Create the tables:
   ```
   npx wrangler d1 execute clipgenie-licence --remote --file=schema.sql
   npx wrangler deploy
   ```
   It prints your server address, like `https://clipgenie-licence.<yourname>.workers.dev`.

## 2. Twitch app (free, no approval)
1. Turn on two-factor login on your Twitch account (Twitch asks for it).
2. Go to https://dev.twitch.tv/console/apps > Register Your Application.
   - Name: Clip Genie
   - OAuth Redirect URL: `https://clipgenie-licence.<yourname>.workers.dev/auth/callback`
   - Category: Application Integration, Client type: Confidential
3. Open it, copy the Client ID, press New Secret and copy the secret. Then:
   ```
   npx wrangler secret put TWITCH_CLIENT_ID
   npx wrangler secret put TWITCH_CLIENT_SECRET
   ```

## 3. Admin dashboard
Tell the server who may open it (your Twitch name, lower case; more names = comma separated):
```
npx wrangler secret put ADMIN_LOGINS
```
Then open `https://clipgenie-licence.<yourname>.workers.dev/admin` and Sign in with Twitch. There you can:
see users, streams added and PCs; give paid time (+30 days / +1 year) or end it; reset someone's trial; make
someone owner (unlimited); block / unblock an account; and flip the switches below without a deploy.
Updating an existing database: `npx wrangler d1 execute clipgenie-licence --remote --command "ALTER TABLE accounts ADD COLUMN banned INTEGER NOT NULL DEFAULT 0"`
then `--file=schema.sql` again (adds the new tables).

## 4. Make yourself unlimited
Sign in once from a compiled build, then:
```
npx wrangler d1 execute clipgenie-licence --remote --command "UPDATE accounts SET owner = 1 WHERE login = 'xhotgamerdude'"
```
A customer who paid: `UPDATE accounts SET paid_until = <unix time> WHERE login = '<their twitch name>'`
(automatic once payments are set up).

## 5. Build the public engine with your server baked in
```
python\python.exe tools\build_engine.py --licence-url=https://clipgenie-licence.<yourname>.workers.dev
python\python.exe tools\build_tester.py --compiled
```

## Switches (easiest: the admin dashboard; or wrangler.toml + `npx wrangler deploy`. The dashboard wins. No app update needed)
- `TRIAL_LIMIT = "3"`: free streams per account / PC.
- `TRIALS_OPEN = "0"`: no more free trials for anyone (paid users keep working).
- `MIN_VERSION = "2.37.0"`: older copies must update before adding a stream (switches old builds off).
- `BUY_URL`: the payment page shown when the trial is used up.
- `TRIAL_WATERMARK = "1"`: trial videos get the Clip Genie FREE TRIAL logo ("0" = no mark).
- `TRIAL_AI = "0"`: no AI features in the trial ("1" = AI allowed).

## Testing on this PC (no real Twitch)
`.dev.vars` (not uploaded) holds the Twitch test app keys and `ADMIN_LOGINS` (add `DEV_FAKE_LOGIN=1` for a pretend
sign-in): `npx wrangler dev --ip 127.0.0.1` then runs on http://127.0.0.1:8787 (admin page at /admin). Start an engine from source with `CLIPGENIE_TRIAL=1 CLIPGENIE_LICENCE_URL=http://127.0.0.1:8787`.
The pretend sign-in only works on 127.0.0.1, never on the real server.

## What it can't stop
- Copies handed out BEFORE this (tester zips 2.35.x / 2.36.x from source) have no check and keep working offline.
- Someone very determined could still crack a compiled app; this makes it a lot of work, not impossible.

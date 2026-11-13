-- Clip Genie licence server (Cloudflare D1). Trial = 3 streams/clips per Twitch account AND per PC.
CREATE TABLE IF NOT EXISTS accounts (
  twitch_id TEXT PRIMARY KEY,
  login TEXT, display_name TEXT,
  created INTEGER NOT NULL,
  paid_until INTEGER NOT NULL DEFAULT 0,   -- unix seconds; > now = paid
  owner INTEGER NOT NULL DEFAULT 0,        -- 1 = never limited (the creator)
  banned INTEGER NOT NULL DEFAULT 0        -- 1 = blocked from adding streams
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,             -- sha256 of the token the app keeps
  twitch_id TEXT NOT NULL, pc_id TEXT NOT NULL, created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pcs (           -- each hashed PC part (Windows id, board, disk) -> one PC
  part TEXT PRIMARY KEY, pc_id TEXT NOT NULL, created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS uses (          -- one row per stream/clip added (the same one again is free)
  twitch_id TEXT NOT NULL, pc_id TEXT NOT NULL, item TEXT NOT NULL, at INTEGER NOT NULL,
  PRIMARY KEY (twitch_id, item)
);
CREATE INDEX IF NOT EXISTS uses_pc ON uses (pc_id);
CREATE TABLE IF NOT EXISTS auth_states (   -- sign-in in progress (10 minutes)
  state TEXT PRIMARY KEY, parts TEXT NOT NULL, created INTEGER NOT NULL, token TEXT, user TEXT
);
CREATE TABLE IF NOT EXISTS settings (      -- switches set from the admin dashboard (override wrangler.toml [vars])
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_sessions (   -- admin dashboard sign-ins (cookie), 7 days
  token_hash TEXT PRIMARY KEY, twitch_id TEXT NOT NULL, created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS guest_ips (     -- "Continue without Twitch": which PCs started a guest trial from which connection
  ip_hash TEXT NOT NULL, pc_id TEXT NOT NULL, created INTEGER NOT NULL, PRIMARY KEY (ip_hash, pc_id)
);

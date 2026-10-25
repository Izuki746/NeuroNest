CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, started TIMESTAMPTZ DEFAULT now(),
  card TEXT, age INT, lesion JSONB, traits JSONB, kind TEXT);   -- kind: 'live' | 'population'

CREATE TABLE IF NOT EXISTS brain_state (time TIMESTAMPTZ NOT NULL, session_id TEXT, sim_day INT,
  activity REAL[]);                                    -- 76 values, sampled ~5 Hz live
CREATE TABLE IF NOT EXISTS attempts (time TIMESTAMPTZ NOT NULL, session_id TEXT, sim_day INT,
  effort REAL, depth REAL, speed REAL, hold_ms REAL, arm_output REAL, arm_function REAL,
  stim_offset_ms REAL, stim_verdict TEXT, gate REAL, source TEXT);
CREATE TABLE IF NOT EXISTS stimulation (time TIMESTAMPTZ NOT NULL, session_id TEXT, sim_day INT,
  actor TEXT, region TEXT, kind TEXT, intensity REAL, offset_ms REAL);
CREATE TABLE IF NOT EXISTS daily (time TIMESTAMPTZ NOT NULL, session_id TEXT, sim_day INT,
  arm_function REAL, mean_effort REAL, attempts INT, window_level REAL, stim_count INT, plan JSONB);
CREATE TABLE IF NOT EXISTS weights (time TIMESTAMPTZ NOT NULL, session_id TEXT, sim_day INT,
  src SMALLINT, dst SMALLINT, strength REAL);          -- only changed edges (|delta| > 1e-3)
CREATE TABLE IF NOT EXISTS forecasts (time TIMESTAMPTZ NOT NULL, session_id TEXT, sim_day INT,
  p10 REAL, p50 REAL, p90 REAL, n_alive INT, method TEXT);   -- method: 'twins' | 'ml' | 'proportional'
-- Make each time-series table a hypertable (try the first form; if unsupported, use the second):
--   SELECT create_hypertable('attempts', by_range('time'), if_not_exists => TRUE);
--   SELECT create_hypertable('attempts', 'time', if_not_exists => TRUE);
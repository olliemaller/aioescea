# Changelog

## 0.1.3

Fixes.

- The web login (and saving timers) hung until it timed out on a real
  fire, while the same login worked in a browser. aiohttp sends a POST's
  headers and form in separate TCP segments; the fire's server reads only
  the first and then waits for a body it never sees. POSTs are now sent as
  one raw write, and the reply is read only until it's complete, since the
  server doesn't always close the connection.

## 0.1.2

Fixes.

- The web page timeouts are longer: 30 s to read a page, 60 s to log in or
  save timers (were 10 s and 30 s). Fire web servers often take 10-20 s per
  page, so a working login was reported as "didn't answer".

## 0.1.1

Fixes.

- Turning off a fire that's already off no longer starts the 66 s busy
  window, so a routine "make sure it's off" can't block turning it on.
- Half degrees round up (22.5 °C sets 23 °C). Python's `round()` rounded
  them to the nearest even number, so 22.5 became 22.
- Saving a timer after the web login token expired now logs in again and
  resends, instead of failing with "did not save".
- The web page answering 401 or 403 raises `EsceaAuthError`, not a
  connection error.
- NCAPI temperatures sent as strings or decimals (`"225"`, `225.0`) parse.
- `EsceaNcapiClient` and `async_probe` take a `timeout`, so a quick probe
  doesn't wait the full 10 s on an address that isn't a Wi-Fi fire.
- New `round_half_up()` helper.

## 0.1.0

First release.

- UDP control: status, on, off, target temperature, and normal, boost and
  flame effect modes, with retries and one request per fire at a time.
- Never sends a command it wasn't asked for, so it can't re-light a fire
  turned off with the remote or app.
- Refuses commands during the 66 s lighting window (except turning off), and
  refuses a mode change that would light a fire that's off unless asked.
- Discovery by broadcast on any list of broadcast addresses, and finding one
  fire by serial number after its IP address changes.
- Web page client: the fire's clock, both timers (read and write) and the
  diagnostics page.
- Generation 3 Wi-Fi fires through Escea's NCAPI (HTTP and JSON): state,
  room temperature, remote presence, stored errors, and on/off, boost, flame
  effect and target temperature in one request.
- `aioescea` command-line tool.

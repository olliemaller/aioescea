# Changelog

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

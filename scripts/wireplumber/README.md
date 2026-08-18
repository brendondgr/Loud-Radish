# WirePlumber drop-in: stop tools writing volume into shared memory

Install once per machine:

```bash
mkdir -p ~/.config/wireplumber/wireplumber.conf.d
cp scripts/wireplumber/50-no-tool-volume-memory.conf ~/.config/wireplumber/wireplumber.conf.d/
systemctl --user restart wireplumber
```

## What it prevents

WirePlumber remembers a stream's volume and restores it next time. The bucket it files that under is
chosen by `formKey` in `/usr/share/wireplumber/scripts/node/state-stream.lua`, and the priority list
**begins with `media.role`** — ahead of `application.id` and `application.name`.

`pw-cat --help` says of the role: *"(default Music)"*. So every `pw-play` files itself under the
shared `Music` bucket rather than its own name, and every application declaring that role inherits
whatever the last tool left there. Six seconds of `pw-play --volume=0.02` while testing audio
capture is enough to leave a desktop at −34 dB indefinitely.

The symptom is confusing in a specific way, which is what makes it worth a config file rather than a
rule somebody has to remember:

* **The mixer shows a healthy number.** PipeWire multiplies two gains. `channelVolumes` is the one
  every mixer displays and lets you drag; `volume` is a scalar that nothing in the KDE interface
  shows or changes. Measured: `channelVolumes` 0.2847 (shown as 66%) × `volume` 0.0200 = −44.9 dB.
* **Only some applications go quiet.** A browser keeps working, because it has an entry under its
  own name and never reaches the role fallback. A video player and a music client's audio-only mode
  do not, and go silent together.

`state.restore-props = false` is WirePlumber's own per-stream opt-out — its `bluez.lua` and
`alsa.lua` monitors set exactly this property. The drop-in applies it to the command-line tools, so
they are played and forgotten, which is what a diagnostic should do.

## Verified

Against the real daemon, before and after installing it:

| | `media.role:Music` after `pw-play --volume=0.02` |
|---|---|
| without the drop-in | `volume: 0.020000` — the fault |
| with the drop-in | `volume: 1.000000` — unchanged, twice running |

Every other saved entry is untouched: the rule matches six tool names and nothing else, and the 115
existing entries — including per-application volumes the user set deliberately — survive a restart
unchanged.

## If it has already happened

`scripts/repair_stream_volumes.py --fix` resets the leaked role entries and restarts WirePlumber.

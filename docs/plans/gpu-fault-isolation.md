# GPU Fault Isolation

*Written 2026-09-18. Branch `fix/gpu-crash-isolation`.*

### 1. Introduction

On 2026-09-18 two window recordings of a Zoom call died partway through. Neither was a capture
fault. Both times the kernel logged an `amdgpu [gfxhub] page fault` against the server's own
`python3` process, and the ROCm runtime answered with `Memory access fault by GPU node-1 … Page not
present`, which aborts the whole process. Everything in that process went with it: the live
transcript, the session teardown, the mux, and the transcription pass. The `gst-launch-1.0` video
recorder survived as an orphan that wrote nothing, and its `video.webm` was never finalised.

Both faults happened while the local language model was generating. That model is gemma-4-26B on
llama.cpp's **Vulkan** backend, reached through DashLLM at `localhost:4000`, and it shares the
gfx1151 iGPU with faster-whisper on **ROCm**. Every earlier window recording sent the language model
to a remote machine and never faulted. Two further facts: the two long generations (1,085 and 2,687
tokens) were the context worker's rolling summary, which never asked the model to skip its
reasoning; and a measurement against the running server showed Gemma spends about 240 tokens
thinking about a one-sentence request unless it is sent `chat_template_kwargs.enable_thinking =
false`.

The approach puts prevention first and survival second. **Prevention:** no request from this
application asks a model to think, so every generation is about ten times shorter. When the language
model is local and the speech model is on the GPU, the two also take turns on the GPU, so the
overlap that produced both faults no longer happens in the ordinary case. **Survival:** the speech
model runs in its own process, so a GPU fault costs one restarted worker instead of the recording.
The video recorder is also tied to the server's lifetime, and any recorder orphaned by a past crash
is finalised at start-up.

### 2. Gaps & Unanswered Questions

- **Driver, firmware, or CTranslate2?** Two faults do not say which layer is at fault. The fix is
  deliberately indifferent to that: fewer overlaps and a fault that stays inside one process help
  whichever layer it is. *Assumption:* no kernel parameter is changed. `amdgpu.cwsr_enable=0` is a
  candidate the user can try, and it is recorded in the checklist rather than applied, because
  changing kernel parameters is the user's decision.
- **How long may the speech model wait for the language model?** The capture queue holds about 6.4
  s of audio (`QUEUE_CAPACITY = 200` frames × 32 ms). *Assumption:* the speech model waits at most
  4 s (`asr.gpu_turn_wait_s`) and then runs alongside the language model anyway. A long chat answer
  can therefore still overlap. That is survivable once the worker is isolated, and it never drops
  audio.
- **Is a `localhost` endpoint really on this GPU?** DashLLM can route to a remote machine.
  *Assumption:* a loopback endpoint in `local` mode is treated as sharing the GPU (the default
  `llm.local.shares_gpu = "auto"`). Being wrong costs a few hundred milliseconds of waiting and
  loses nothing. The setting can be forced either way.
- **Reasoning in the assistant's chat.** The user asked for no thinking at all. *Assumption:* one
  switch, `llm.generation.disable_reasoning` (default on), covers every request: polish, summaries,
  glossary, chat and dictation. The per-pass `polish.disable_reasoning` is retired.
- **What if the worker faults repeatedly?** *Assumption:* after three crashes within ten minutes,
  the worker reloads on the CPU (`int8`) for the rest of that load and says so. Transcription slows
  down but does not stop.

### 3. Hierarchical Step-by-Step Instructions

#### Step 1: One provider-wide "do not think" switch — **Complete**
- **Locations**: `config/schema.py` (`LlmGenerationConfig.disable_reasoning`, the retired
  `PolishConfig.disable_reasoning`); `config/store.py` (`RETIRED_PATHS`);
  `services/llm/openai_compatible.py` (`NO_REASONING_EXTRAS`, the request body, one retry
  without the fields per endpoint); `services/llm/registry.py` (`build_llm`);
  `services/polish/{worker,prompts}.py` and `services/dictation/{pipeline,prompts}.py` (their
  private copies and retry removed).
- **Rationale**: The fields were sent by two of the five callers, and the three that did not send
  them (summaries, glossary, chat) produced both long generations. Putting the fields in the client
  covers every caller and removes three copies of the same rule.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated, push changes to GitHub stating: GPU Fault Isolation (1 / 5) Complete: Every request asks the model not to think.

#### Step 2: The speech model in its own process — **Complete**
- **Locations**: `services/asr/worker.py` (new: the child's serve loop), `services/asr/isolated.py`
  (new: `IsolatedAsrBackend`, which spawns, supervises, restarts and falls back to the CPU),
  `services/asr/registry.py` (`register(..., isolate=True)` for faster-whisper, `build_backend`),
  `config/schema.py` (`AsrConfig.process_isolation`), `services/asr/lifecycle.py` (a restart
  notice), `services/session/{manager,frames}.py` and `services/session/degradation.py`
  (`asr_worker_restarted`, `asr_fell_back_to_cpu`).
- **Rationale**: A GPU fault cannot be caught in the process that caused it. It can only be kept
  out of the process that matters. The child reads requests from a socket, so the server's death is
  end-of-file and the child exits by itself.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated, push changes to GitHub stating: GPU Fault Isolation (2 / 5) Complete: The speech model runs in a worker process that is restarted after a fault.

#### Step 3: Taking turns on a shared GPU — **Complete**
- **Locations**: `services/gpu/turns.py` (new: `GpuTurns`, `shared_turns`),
  `services/llm/openai_compatible.py` (holds a turn for the life of a stream),
  `services/llm/registry.py` (`shares_gpu`), `services/asr/lifecycle.py` (waits for a turn when
  the model runs on the GPU), `config/schema.py` (`LlmLocalConfig.shares_gpu`,
  `AsrConfig.gpu_turn_wait_s`).
- **Rationale**: This is the preventive half. Several language-model requests may run together, but
  never alongside a speech pass. The speech model's wait is bounded so that taking turns can never
  cost audio.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated, push changes to GitHub stating: GPU Fault Isolation (3 / 5) Complete: A local language model and the GPU speech model take turns.

#### Step 4: A recorder that cannot outlive the server — **Complete**
- **Locations**: `services/capture/lifetime.py` (new: a long-lived spawner thread, `setpriv
  --pdeathsig INT`, and `finalise_orphans`), `services/capture/recorder.py` (`start`),
  `main.py` (`lifespan` runs the sweep).
- **Rationale**: `PR_SET_PDEATHSIG` fires when the *thread* that forked the child exits, and a
  recorder can be restarted from the short-lived watcher thread. Spawning from one thread that lives
  as long as the server makes the signal mean "the server died". The start-up sweep covers a server
  killed before this existed.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated, push changes to GitHub stating: GPU Fault Isolation (4 / 5) Complete: The video recorder finalises its file when the server dies.

#### Step 5: Documentation and verification — **Complete**
- **Locations**: `docs/documentation.md` (D-071 to D-074), `docs/architecture.md`,
  `docs/data-flow.md`, `docs/structure.md`, `docs/checklist.md`, `docs/plans/README.md`.
- **Rationale**: The ASR is no longer in the server's process, and the documentation has to say
  so.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated, push changes to GitHub stating: GPU Fault Isolation (5 / 5) Complete: Documented process isolation, GPU turns and the reasoning switch.

### 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Reasoning switch | One setting; the fields are sent by the client to every endpoint | `web/backend/app/services/llm/openai_compatible.py` |
| ASR worker | The child process: load, transcribe, unload, exit on EOF | `web/backend/app/services/asr/worker.py` |
| Isolated backend | Spawn, supervise, restart, CPU fallback, hang watchdog | `web/backend/app/services/asr/isolated.py` |
| GPU turns | Several LLM requests or one bounded speech pass, never both | `web/backend/app/services/gpu/turns.py` |
| Recorder lifetime | Death signal from a permanent spawner thread; orphan sweep | `web/backend/app/services/capture/lifetime.py` |
| Tests | Reasoning fields and retry; worker restart after SIGKILL, hang, CPU fallback; turn fairness and bounds; orphan detection | `tests/assistant/test_reasoning_switch.py`, `tests/transcription/test_isolated_asr.py`, `tests/transcription/test_gpu_turns.py`, `tests/transcription/test_recorder_lifetime.py` |

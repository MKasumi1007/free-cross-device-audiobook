from __future__ import annotations

import uvicorn

from .app import create_app
from .cleanup import clean_expired_generation_files
from .firebase_rest import FirebasePublicConfig, FirebaseRestClient
from .library import LocalLibrary
from .paths import AGENT_PORT, data_root
from .pairing import FirebasePairingProvider
from .picker import NativeBookPicker, NativeVoicePicker
from .preview import VoicePreviewService, default_qwen_factory
from .resources import ResourcePolicy
from .task_cloud import FirestoreWorkerTasks
from .voice import VoiceRegistry
from .worker import MacGenerationWorker


def main() -> None:
    root = data_root()
    library = LocalLibrary(root / "books", NativeBookPicker())
    voices = VoiceRegistry(root / "voices", NativeVoicePicker())
    clean_expired_generation_files(root / "generation")
    policy = ResourcePolicy(root / "generation-settings.json")
    config = FirebasePublicConfig.load()
    client = FirebaseRestClient(config) if config else None
    pairing = FirebasePairingProvider(client)
    worker = None
    if client:
        worker = MacGenerationWorker(
            tasks=FirestoreWorkerTasks(client),
            library=library,
            voices=voices,
            policy=policy,
            generator_factory=default_qwen_factory,
            work_root=root / "generation",
            repository="MKasumi1007/free-cross-device-audiobook",
        )
    previews = VoicePreviewService(
        voices,
        default_qwen_factory,
        policy=policy,
        lock_path=root / "generation/active-task.lock",
        generation_model_loaded=lambda: bool(worker and worker.model_loaded()),
    )
    if worker:
        worker.start()
    app = create_app(
        library=library,
        pairing=pairing,
        voices=voices,
        previews=previews,
        worker_model_loaded=lambda: bool(worker and worker.model_loaded()),
        worker=worker,
    )
    try:
        uvicorn.run(app, host="127.0.0.1", port=AGENT_PORT, log_level="info")
    finally:
        previews.unload()
        if worker:
            worker.stop()


if __name__ == "__main__":
    main()

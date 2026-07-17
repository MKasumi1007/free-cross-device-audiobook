from __future__ import annotations

from pathlib import Path

import uvicorn

from .app import create_app
from .cleanup import clean_expired_generation_files
from .firebase_rest import FirebasePublicConfig, FirebaseRestClient
from .library import LocalLibrary
from .pairing import FirebasePairingProvider
from .picker import NativeBookPicker, NativeVoicePicker
from .preview import VoicePreviewService, default_qwen_factory
from .resources import ResourcePolicy
from .task_cloud import FirestoreWorkerTasks
from .voice import VoiceRegistry
from .worker import MacGenerationWorker


def main() -> None:
    data_root = Path.home() / "Library/Application Support/听见书页"
    library = LocalLibrary(data_root / "books", NativeBookPicker())
    voices = VoiceRegistry(data_root / "voices", NativeVoicePicker())
    clean_expired_generation_files(data_root / "generation")
    policy = ResourcePolicy(data_root / "generation-settings.json")
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
            work_root=data_root / "generation",
            repository="MKasumi1007/free-cross-device-audiobook",
        )
    previews = VoicePreviewService(
        voices,
        default_qwen_factory,
        policy=policy,
        lock_path=data_root / "generation/active-task.lock",
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
    )
    try:
        uvicorn.run(app, host="127.0.0.1", port=17832, log_level="info")
    finally:
        previews.unload()
        if worker:
            worker.stop()


if __name__ == "__main__":
    main()

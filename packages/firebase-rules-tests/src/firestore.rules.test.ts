import {
  assertFails,
  assertSucceeds,
  initializeTestEnvironment,
  type RulesTestEnvironment,
} from "@firebase/rules-unit-testing";
import {
  Bytes,
  Timestamp,
  doc,
  getDoc,
  serverTimestamp,
  setDoc,
  updateDoc,
  writeBatch,
} from "firebase/firestore";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

const PROJECT_ID = "demo-free-cross-device-audiobook";
let environment: RulesTestEnvironment;

function ownerDb(uid = "owner-a") {
  return environment.authenticatedContext(uid, { email: `${uid}@example.test` }).firestore();
}

function workerDb(uid = "worker-a") {
  return environment.authenticatedContext(uid, { firebase: { sign_in_provider: "anonymous" } }).firestore();
}

async function seed(path: string, value: Record<string, unknown>): Promise<void> {
  await environment.withSecurityRulesDisabled(async (context) => {
    await setDoc(doc(context.firestore(), path), value);
  });
}

function bookData(ownerUid: string, bookId = "book-a") {
  return {
    owner_uid: ownerUid,
    book_id: bookId,
    title: "测试书",
    source_sha256: "a".repeat(64),
    publication_mode: "LOCAL_ONLY",
  };
}

function taskData(ownerUid: string, taskId = "task-a") {
  return {
    owner_uid: ownerUid,
    task_id: taskId,
    book_id: "book-a",
    status: "QUEUED",
    priority: 300,
    attempt_id: 0,
    deletion_generation: 0,
    created_at: Timestamp.now(),
    updated_at: Timestamp.now(),
  };
}

beforeAll(async () => {
  environment = await initializeTestEnvironment({ projectId: PROJECT_ID });
});

afterEach(async () => {
  await environment.clearFirestore();
});

afterAll(async () => {
  await environment.cleanup();
});

describe("owner isolation", () => {
  it("rejects anonymous reads, cross-user reads, and forged owner fields", async () => {
    const path = "users/owner-a/books/book-a";
    await seed(path, bookData("owner-a"));

    await assertFails(getDoc(doc(environment.unauthenticatedContext().firestore(), path)));
    await assertFails(getDoc(doc(ownerDb("owner-b"), path)));
    await assertFails(setDoc(doc(ownerDb("owner-a"), "users/owner-a/books/book-b"), bookData("owner-b", "book-b")));
  });

  it("allows an owner to write and read only their own book", async () => {
    const database = ownerDb();
    const reference = doc(database, "users/owner-a/books/book-a");
    await assertSucceeds(setDoc(reference, bookData("owner-a")));
    await assertSucceeds(getDoc(reference));
  });
});

describe("optimistic progress", () => {
  it("keeps a two-device bookshelf in sync without allowing stale progress", async () => {
    const deviceA = ownerDb();
    const deviceB = ownerDb();
    const bookPath = "users/owner-a/books/book-a";
    await assertSucceeds(setDoc(doc(deviceA, bookPath), bookData("owner-a")));
    const bookOnDeviceB = await assertSucceeds(getDoc(doc(deviceB, bookPath)));
    expect(bookOnDeviceB.data()?.title).toBe("测试书");

    const progressPath = "users/owner-a/progress/book-a";
    await assertSucceeds(setDoc(doc(deviceA, progressPath), {
      owner_uid: "owner-a",
      book_id: "book-a",
      chapter_id: "chapter-a",
      segment_id: "segment-a",
      audio_offset_seconds: 0,
      device_id: "device-a",
      version: 1,
      updated_at: serverTimestamp(),
    }));
    await assertSucceeds(updateDoc(doc(deviceB, progressPath), {
      segment_id: "segment-b",
      device_id: "device-b",
      version: 2,
      updated_at: serverTimestamp(),
    }));
    await assertFails(updateDoc(doc(deviceA, progressPath), {
      segment_id: "stale-segment",
      version: 2,
      updated_at: serverTimestamp(),
    }));
    const current = await assertSucceeds(getDoc(doc(deviceA, progressPath)));
    expect(current.data()?.segment_id).toBe("segment-b");
    expect(current.data()?.version).toBe(2);
  });

  it("accepts the next version and rejects an old device overwrite", async () => {
    const database = ownerDb();
    const reference = doc(database, "users/owner-a/progress/book-a");
    await assertSucceeds(setDoc(reference, {
      owner_uid: "owner-a",
      book_id: "book-a",
      chapter_id: "chapter-a",
      segment_id: "segment-a",
      audio_offset_seconds: 0,
      device_id: "device-a",
      version: 1,
      updated_at: serverTimestamp(),
    }));
    await assertSucceeds(updateDoc(reference, {
      segment_id: "segment-b",
      version: 2,
      updated_at: serverTimestamp(),
    }));
    await assertFails(updateDoc(reference, {
      segment_id: "stale-segment",
      version: 2,
      updated_at: serverTimestamp(),
    }));
  });
});

describe("multi-book scheduling", () => {
  it("lets the owner pause an inactive retry and restore it to the active queue", async () => {
    const path = "users/owner-a/generationRequests/task-a";
    await seed(path, {
      ...taskData("owner-a"),
      status: "FAILED_RETRYABLE",
      priority: 300,
      error_code: "TEMPORARY_FAILURE",
    });
    const task = doc(ownerDb(), path);

    await assertSucceeds(updateDoc(task, {
      status: "PAUSED",
      pause_reason: "INACTIVE_48_HOURS",
      priority: 100,
      updated_at: serverTimestamp(),
    }));
    await assertSucceeds(updateDoc(task, {
      status: "QUEUED",
      pause_reason: null,
      priority: 300,
      updated_at: serverTimestamp(),
    }));
    await assertFails(updateDoc(task, {
      attempt_id: 99,
      updated_at: serverTimestamp(),
    }));
  });
});

describe("worker permissions", () => {
  it("publishes text metadata only for a rights-confirmed book", async () => {
    await seed("workerLinks/worker-a", {
      worker_uid: "worker-a",
      owner_uid: "owner-a",
      worker_type: "MAC_AGENT",
      scopes: ["generation"],
      revoked_at: null,
    });
    const publicPath = "users/owner-a/books/public-book";
    await seed(publicPath, {
      ...bookData("owner-a", "public-book"),
      publication_mode: "PUBLIC_RIGHTS_CONFIRMED",
    });
    await assertSucceeds(updateDoc(doc(workerDb(), publicPath), {
      text_status: "READY",
      text_asset_id: 12,
      text_asset_name: "book-public-text.json.gz",
      text_asset_url: "https://example.test/book.json.gz",
      text_sha256: "b".repeat(64),
      text_byte_size: 500,
      text_schema_version: 1,
      updated_at: serverTimestamp(),
    }));

    const privatePath = "users/owner-a/books/private-book";
    await seed(privatePath, bookData("owner-a", "private-book"));
    await assertFails(updateDoc(doc(workerDb(), privatePath), {
      text_status: "READY",
      text_asset_id: 13,
      text_asset_name: "forbidden.json.gz",
      text_asset_url: "https://example.test/forbidden.json.gz",
      text_sha256: "c".repeat(64),
      text_byte_size: 500,
      text_schema_version: 1,
      updated_at: serverTimestamp(),
    }));
    await seed(`users/owner-a/privateAssets/${"d".repeat(64)}`, {
      owner_uid: "owner-a",
      asset_key: "d".repeat(64),
      book_id: "private-book",
      kind: "BOOK_TEXT",
      status: "READY",
    });
    await assertSucceeds(updateDoc(doc(workerDb(), privatePath), {
      text_status: "READY",
      private_text_key: "d".repeat(64),
      private_text_name: "book-private-text.json.gz",
      private_text_sha256: "e".repeat(64),
      private_text_byte_size: 500,
      private_text_parts: 1,
      text_schema_version: 1,
      updated_at: serverTimestamp(),
    }));
  });

  it("keeps private asset bytes owner-only and revokes worker access", async () => {
    await seed("workerLinks/worker-a", {
      worker_uid: "worker-a",
      owner_uid: "owner-a",
      worker_type: "MAC_AGENT",
      scopes: ["generation"],
      revoked_at: null,
    });
    const assetKey = "f".repeat(64);
    const assetPath = `users/owner-a/privateAssets/${assetKey}`;
    await assertSucceeds(setDoc(doc(workerDb(), assetPath), {
      owner_uid: "owner-a",
      asset_key: assetKey,
      book_id: "book-a",
      task_id: "task-a",
      asset_name: "audio-private.m4a",
      kind: "AUDIO",
      status: "UPLOADING",
      content_type: "audio/mp4",
      byte_size: 4,
      part_count: 1,
      sha256: "a".repeat(64),
      created_at: serverTimestamp(),
      updated_at: serverTimestamp(),
    }));
    const partPath = `${assetPath}/parts/0000`;
    await assertSucceeds(setDoc(doc(workerDb(), partPath), {
      owner_uid: "owner-a",
      asset_key: assetKey,
      part_index: 0,
      byte_size: 4,
      payload: Bytes.fromUint8Array(new Uint8Array([1, 2, 3, 4])),
      created_at: serverTimestamp(),
      updated_at: serverTimestamp(),
    }));
    await assertSucceeds(updateDoc(doc(workerDb(), assetPath), {
      status: "READY",
      updated_at: serverTimestamp(),
    }));

    await assertSucceeds(getDoc(doc(ownerDb(), partPath)));
    await assertFails(getDoc(doc(ownerDb("owner-b"), partPath)));
    await assertFails(getDoc(doc(environment.unauthenticatedContext().firestore(), partPath)));

    await seed("workerLinks/worker-a", {
      worker_uid: "worker-a",
      owner_uid: "owner-a",
      worker_type: "MAC_AGENT",
      scopes: ["generation"],
      revoked_at: Timestamp.now(),
    });
    await assertFails(getDoc(doc(workerDb(), partPath)));
  });

  it("allows task lease fields but blocks progress and unrelated task fields", async () => {
    await seed("workerLinks/worker-a", {
      worker_uid: "worker-a",
      owner_uid: "owner-a",
      worker_type: "MAC_AGENT",
      scopes: ["generation"],
      revoked_at: null,
    });
    const taskPath = "users/owner-a/generationRequests/task-a";
    await seed(taskPath, taskData("owner-a"));
    const database = workerDb();
    const task = doc(database, taskPath);

    await assertSucceeds(getDoc(task));
    await assertSucceeds(updateDoc(task, {
      status: "LEASED",
      attempt_id: 1,
      lease_owner: "worker-a",
      lease_token: "a-secure-lease-token-with-24-chars",
      lease_deadline: Timestamp.fromMillis(Date.now() + 120_000),
      pause_reason: null,
      updated_at: serverTimestamp(),
    }));
    await assertFails(updateDoc(task, { priority: 999 }));
    await assertFails(setDoc(doc(database, "users/owner-a/progress/book-a"), {
      owner_uid: "owner-a",
      book_id: "book-a",
      version: 1,
    }));
  });

  it("denies an already revoked worker", async () => {
    await seed("workerLinks/worker-a", {
      worker_uid: "worker-a",
      owner_uid: "owner-a",
      worker_type: "MAC_AGENT",
      scopes: ["generation"],
      revoked_at: Timestamp.now(),
    });
    await seed("users/owner-a/generationRequests/task-a", taskData("owner-a"));
    await assertFails(getDoc(doc(workerDb(), "users/owner-a/generationRequests/task-a")));
  });

  it("recovers only an expired lease and fences the old attempt", async () => {
    await seed("workerLinks/worker-a", {
      worker_uid: "worker-a",
      owner_uid: "owner-a",
      worker_type: "MAC_AGENT",
      scopes: ["generation"],
      revoked_at: null,
    });
    const taskPath = "users/owner-a/generationRequests/task-a";
    await seed(taskPath, {
      ...taskData("owner-a"),
      status: "GENERATING",
      attempt_id: 1,
      lease_owner: "worker-a",
      lease_token: "old-secure-lease-token-123456",
      lease_deadline: Timestamp.fromMillis(Date.now() - 60_000),
    });
    const task = doc(workerDb(), taskPath);
    await assertSucceeds(updateDoc(task, {
      status: "LEASED",
      attempt_id: 2,
      lease_owner: "worker-a",
      lease_token: "new-secure-lease-token-123456",
      lease_deadline: Timestamp.fromMillis(Date.now() + 120_000),
      pause_reason: null,
      updated_at: serverTimestamp(),
    }));
    await assertFails(updateDoc(task, {
      status: "GENERATING",
      attempt_id: 1,
      lease_owner: "worker-a",
      lease_token: "old-secure-lease-token-123456",
      lease_deadline: Timestamp.fromMillis(Date.now() + 120_000),
      updated_at: serverTimestamp(),
    }));
  });

  it("does not let a worker steal an unexpired lease", async () => {
    await seed("workerLinks/worker-a", {
      worker_uid: "worker-a",
      owner_uid: "owner-a",
      worker_type: "MAC_AGENT",
      scopes: ["generation"],
      revoked_at: null,
    });
    const taskPath = "users/owner-a/generationRequests/task-a";
    await seed(taskPath, {
      ...taskData("owner-a"),
      status: "GENERATING",
      attempt_id: 1,
      lease_owner: "worker-a",
      lease_token: "current-secure-lease-token",
      lease_deadline: Timestamp.fromMillis(Date.now() + 120_000),
    });
    await assertFails(updateDoc(doc(workerDb(), taskPath), {
      status: "LEASED",
      attempt_id: 2,
      lease_owner: "worker-a",
      lease_token: "stolen-secure-lease-token-123",
      lease_deadline: Timestamp.fromMillis(Date.now() + 240_000),
      pause_reason: null,
      updated_at: serverTimestamp(),
    }));
  });

  it("resumes only automatic pauses and never a user pause", async () => {
    await seed("workerLinks/worker-a", {
      worker_uid: "worker-a",
      owner_uid: "owner-a",
      worker_type: "MAC_AGENT",
      scopes: ["generation"],
      revoked_at: null,
    });
    const automaticPath = "users/owner-a/generationRequests/automatic";
    await seed(automaticPath, {
      ...taskData("owner-a", "automatic"),
      status: "PAUSED",
      pause_reason: "MEMORY_PRESSURE",
      attempt_id: 1,
    });
    await assertSucceeds(updateDoc(doc(workerDb(), automaticPath), {
      status: "LEASED",
      attempt_id: 2,
      lease_owner: "worker-a",
      lease_token: "resumed-secure-lease-token-123",
      lease_deadline: Timestamp.fromMillis(Date.now() + 120_000),
      pause_reason: null,
      updated_at: serverTimestamp(),
    }));

    const userPath = "users/owner-a/generationRequests/user-paused";
    await seed(userPath, {
      ...taskData("owner-a", "user-paused"),
      status: "PAUSED",
      pause_reason: "USER_PAUSED",
      attempt_id: 1,
    });
    await assertFails(updateDoc(doc(workerDb(), userPath), {
      status: "LEASED",
      attempt_id: 2,
      lease_owner: "worker-a",
      lease_token: "forbidden-secure-lease-token",
      lease_deadline: Timestamp.fromMillis(Date.now() + 120_000),
      pause_reason: null,
      updated_at: serverTimestamp(),
    }));
  });

  it("requires a matching active upload lease before creating READY audio", async () => {
    await seed("workerLinks/worker-a", {
      worker_uid: "worker-a",
      owner_uid: "owner-a",
      worker_type: "MAC_AGENT",
      scopes: ["generation"],
      revoked_at: null,
    });
    await seed("users/owner-a/generationRequests/task-a", {
      ...taskData("owner-a"),
      status: "UPLOADING",
      attempt_id: 2,
      lease_owner: "worker-a",
      lease_token: "current-secure-lease-token",
      lease_deadline: Timestamp.fromMillis(Date.now() + 120_000),
      deletion_generation: 3,
    });
    const reference = doc(workerDb(), "users/owner-a/books/book-a/audioChunks/chunk-a");
    const valid = {
      owner_uid: "owner-a",
      task_id: "task-a",
      book_id: "book-a",
      chunk_id: "chunk-a",
      status: "READY",
      attempt_id: 2,
      lease_token: "current-secure-lease-token",
      deletion_generation: 3,
      storage_mode: "PUBLIC_GITHUB",
      asset_id: 10,
      asset_url: "https://example.test/audio.m4a",
      timeline_asset_id: 11,
      timeline_url: "https://example.test/timeline.json.gz",
    };
    await assertFails(setDoc(reference, { ...valid, lease_token: "old-secure-lease-token" }));
    await assertSucceeds(setDoc(reference, valid));
  });

  it("creates private READY audio only from completed owner assets", async () => {
    await seed("workerLinks/worker-a", {
      worker_uid: "worker-a",
      owner_uid: "owner-a",
      worker_type: "MAC_AGENT",
      scopes: ["generation"],
      revoked_at: null,
    });
    await seed("users/owner-a/generationRequests/task-private", {
      ...taskData("owner-a", "task-private"),
      status: "UPLOADING",
      attempt_id: 1,
      lease_owner: "worker-a",
      lease_token: "private-secure-lease-token",
      lease_deadline: Timestamp.fromMillis(Date.now() + 120_000),
    });
    const audioKey = "a".repeat(64);
    const timelineKey = "b".repeat(64);
    await seed(`users/owner-a/privateAssets/${audioKey}`, {
      owner_uid: "owner-a", asset_key: audioKey, book_id: "book-a", kind: "AUDIO", status: "READY",
    });
    await seed(`users/owner-a/privateAssets/${timelineKey}`, {
      owner_uid: "owner-a", asset_key: timelineKey, book_id: "book-a", kind: "TIMELINE", status: "READY",
    });
    const path = "users/owner-a/books/book-a/audioChunks/chunk-private";
    const privateReady = {
      owner_uid: "owner-a",
      task_id: "task-private",
      book_id: "book-a",
      chunk_id: "chunk-private",
      status: "READY",
      attempt_id: 1,
      lease_token: "private-secure-lease-token",
      deletion_generation: 0,
      storage_mode: "PRIVATE_FIRESTORE",
      asset_id: null,
      asset_url: null,
      timeline_asset_id: null,
      timeline_url: null,
      private_audio_key: audioKey,
      private_timeline_key: timelineKey,
    };
    await assertSucceeds(setDoc(doc(workerDb(), path), privateReady));
    await assertSucceeds(getDoc(doc(ownerDb(), path)));
    await assertFails(getDoc(doc(ownerDb("owner-b"), path)));

    await assertFails(setDoc(
      doc(workerDb(), "users/owner-a/books/book-a/audioChunks/chunk-forged"),
      { ...privateReady, chunk_id: "chunk-forged", private_audio_key: "c".repeat(64) },
    ));
  });

  it("uses a deletion generation barrier when repairing unavailable audio", async () => {
    await seed("workerLinks/worker-a", {
      worker_uid: "worker-a",
      owner_uid: "owner-a",
      worker_type: "MAC_AGENT",
      scopes: ["generation"],
      revoked_at: null,
    });
    await seed("users/owner-a/generationRequests/task-a", {
      ...taskData("owner-a"),
      status: "UPLOADING",
      attempt_id: 2,
      lease_owner: "worker-a",
      lease_token: "current-secure-lease-token",
      lease_deadline: Timestamp.fromMillis(Date.now() + 120_000),
      deletion_generation: 4,
    });
    const path = "users/owner-a/books/book-a/audioChunks/chunk-a";
    await seed(path, {
      owner_uid: "owner-a",
      task_id: "task-a",
      book_id: "book-a",
      chunk_id: "chunk-a",
      chapter_id: "chapter-a",
      status: "READY",
      attempt_id: 1,
      lease_token: "old-secure-lease-token",
      deletion_generation: 3,
      storage_mode: "PUBLIC_GITHUB",
      asset_id: 10,
      asset_url: "https://example.test/audio.m4a",
      timeline_asset_id: 11,
      timeline_url: "https://example.test/timeline.json.gz",
      updated_at: Timestamp.now(),
    });
    const ownerReference = doc(ownerDb(), path);
    await assertSucceeds(updateDoc(ownerReference, {
      status: "FAILED_RETRYABLE",
      error_code: "PLAYBACK_UNAVAILABLE",
      deletion_generation: 4,
      updated_at: serverTimestamp(),
    }));
    await assertFails(updateDoc(doc(ownerDb("owner-b"), path), {
      status: "FAILED_RETRYABLE",
      error_code: "PLAYBACK_UNAVAILABLE",
      deletion_generation: 5,
      updated_at: serverTimestamp(),
    }));
    await assertFails(updateDoc(doc(workerDb(), path), {
      status: "READY",
      attempt_id: 2,
      lease_token: "current-secure-lease-token",
      deletion_generation: 3,
      asset_id: 20,
      updated_at: serverTimestamp(),
      completed_at: serverTimestamp(),
    }));
    await assertSucceeds(updateDoc(doc(workerDb(), path), {
      status: "READY",
      attempt_id: 2,
      lease_token: "current-secure-lease-token",
      deletion_generation: 4,
      asset_id: 20,
      updated_at: serverTimestamp(),
      completed_at: serverTimestamp(),
    }));
  });

  it("deletes remote audio in two phases while preserving reading data", async () => {
    await seed("workerLinks/worker-a", {
      worker_uid: "worker-a",
      owner_uid: "owner-a",
      worker_type: "MAC_AGENT",
      scopes: ["generation"],
      revoked_at: null,
    });
    await seed("users/owner-a/books/book-a", bookData("owner-a"));
    await seed("users/owner-a/books/book-a/chapters/chapter-a", {
      owner_uid: "owner-a", book_id: "book-a", chapter_id: "chapter-a", title: "第一章",
    });
    await seed("users/owner-a/books/book-a/bookmarks/mark-a", {
      owner_uid: "owner-a", book_id: "book-a", bookmark_id: "mark-a", segment_id: "segment-a",
    });
    await seed("users/owner-a/progress/book-a", {
      owner_uid: "owner-a", book_id: "book-a", version: 1, segment_id: "segment-a",
    });
    await seed("users/owner-a/generationRequests/task-a", {
      ...taskData("owner-a"), status: "READY", start_segment_id: "segment-a",
    });
    const chunkPath = "users/owner-a/books/book-a/audioChunks/chunk-a";
    await seed(chunkPath, {
      owner_uid: "owner-a",
      task_id: "task-a",
      book_id: "book-a",
      chunk_id: "chunk-a",
      chapter_id: "chapter-a",
      status: "READY",
      start_segment_id: "segment-a",
      end_segment_id: "segment-b",
      duration_seconds: 600,
      asset_id: 101,
      asset_url: "https://example.test/audio.m4a",
      byte_size: 1000,
      sha256: "a".repeat(64),
      timeline_asset_id: 202,
      timeline_url: "https://raw.githubusercontent.com/owner/repo/book-assets/books/book-a/timeline.json.gz",
      timeline_sha256: "b".repeat(64),
      voice_version: "voice-a",
      deletion_generation: 0,
    });

    const requestId = "delete-a";
    const owner = ownerDb();
    const requestPath = `users/owner-a/audioDeletionRequests/${requestId}`;
    const deleteBatch = writeBatch(owner);
    deleteBatch.update(doc(owner, chunkPath), {
      status: "DELETING",
      deletion_generation: 1,
      deletion_request_id: requestId,
      delete_requested_at: serverTimestamp(),
      updated_at: serverTimestamp(),
    });
    deleteBatch.update(doc(owner, "users/owner-a/generationRequests/task-a"), {
      status: "CANCELLED", pause_reason: null, deletion_generation: 1, updated_at: serverTimestamp(),
    });
    deleteBatch.set(doc(owner, requestPath), {
      owner_uid: "owner-a",
      request_id: requestId,
      book_id: "book-a",
      chunk_id: "chunk-a",
      task_id: "task-a",
      asset_id: 101,
      asset_url: "https://example.test/audio.m4a",
      timeline_asset_id: 202,
      timeline_url: "https://raw.githubusercontent.com/owner/repo/book-assets/books/book-a/timeline.json.gz",
      deletion_generation: 1,
      status: "QUEUED",
      attempt_count: 0,
      created_at: serverTimestamp(),
      updated_at: serverTimestamp(),
    });
    await assertSucceeds(deleteBatch.commit());

    const worker = workerDb();
    await assertSucceeds(updateDoc(doc(worker, requestPath), {
      status: "PROCESSING",
      attempt_count: 1,
      lease_owner: "worker-a",
      lease_token: "deletion-secure-lease-token-123",
      lease_deadline: Timestamp.fromMillis(Date.now() + 120_000),
      retry_not_before: null,
      updated_at: serverTimestamp(),
    }));
    const completeBatch = writeBatch(worker);
    completeBatch.update(doc(worker, requestPath), {
      status: "DONE", updated_at: serverTimestamp(), completed_at: serverTimestamp(),
    });
    completeBatch.update(doc(worker, chunkPath), {
      status: "DELETED",
      asset_id: null,
      asset_url: null,
      timeline_asset_id: null,
      timeline_url: null,
      private_audio_key: null,
      private_timeline_key: null,
      private_audio_parts: 0,
      private_timeline_parts: 0,
      deleted_at: serverTimestamp(),
      updated_at: serverTimestamp(),
    });
    await assertSucceeds(completeBatch.commit());

    expect((await getDoc(doc(owner, "users/owner-a/books/book-a"))).exists()).toBe(true);
    expect((await getDoc(doc(owner, "users/owner-a/books/book-a/chapters/chapter-a"))).exists()).toBe(true);
    expect((await getDoc(doc(owner, "users/owner-a/books/book-a/bookmarks/mark-a"))).exists()).toBe(true);
    expect((await getDoc(doc(owner, "users/owner-a/progress/book-a"))).data()?.segment_id).toBe("segment-a");

    const regenerateBatch = writeBatch(owner);
    regenerateBatch.update(doc(owner, chunkPath), {
      status: "FAILED_RETRYABLE",
      error_code: "REGENERATION_REQUESTED",
      updated_at: serverTimestamp(),
    });
    regenerateBatch.update(doc(owner, "users/owner-a/generationRequests/task-a"), {
      status: "QUEUED",
      pause_reason: null,
      priority: 300,
      deletion_generation: 1,
      start_segment_id: "segment-a",
      retry_not_before: null,
      updated_at: serverTimestamp(),
    });
    await assertSucceeds(regenerateBatch.commit());
  });

  it("rejects a deletion completion without the matching worker lease", async () => {
    await seed("workerLinks/worker-a", {
      worker_uid: "worker-a", owner_uid: "owner-a", worker_type: "MAC_AGENT",
      scopes: ["generation"], revoked_at: null,
    });
    const requestPath = "users/owner-a/audioDeletionRequests/delete-a";
    await seed(requestPath, {
      owner_uid: "owner-a", request_id: "delete-a", book_id: "book-a", chunk_id: "chunk-a",
      task_id: "task-a", deletion_generation: 1, status: "PROCESSING", attempt_count: 1,
      lease_owner: "worker-b", lease_token: "another-worker-secure-token", lease_deadline: Timestamp.fromMillis(Date.now() + 120_000),
    });
    await assertFails(updateDoc(doc(workerDb(), requestPath), {
      status: "DONE", updated_at: serverTimestamp(), completed_at: serverTimestamp(),
    }));
  });
});

describe("pairing", () => {
  it("lets an authenticated anonymous worker create a short-lived hashed request", async () => {
    const database = workerDb();
    const hash = "b".repeat(64);
    await assertSucceeds(setDoc(doc(database, `pairingRequests/${hash}`), {
      code_hash: hash,
      worker_uid: "worker-a",
      owner_uid: null,
      used_at: null,
      attempt_count: 0,
      created_at: serverTimestamp(),
      expires_at: Timestamp.fromMillis(Date.now() + 5 * 60_000),
    }));
  });

  it("binds once in one atomic owner batch", async () => {
    const hash = "c".repeat(64);
    await seed(`pairingRequests/${hash}`, {
      code_hash: hash,
      worker_uid: "worker-a",
      owner_uid: null,
      used_at: null,
      attempt_count: 0,
      created_at: Timestamp.now(),
      expires_at: Timestamp.fromMillis(Date.now() + 5 * 60_000),
    });

    const database = ownerDb();
    await assertSucceeds(setDoc(doc(database, "pairingAttempts/owner-a"), {
      owner_uid: "owner-a",
      attempt_count: 1,
      last_pairing_hash: hash,
      updated_at: serverTimestamp(),
    }));
    const batch = writeBatch(database);
    batch.update(doc(database, `pairingRequests/${hash}`), {
      owner_uid: "owner-a",
      used_at: serverTimestamp(),
      attempt_count: 1,
    });
    batch.set(doc(database, "workerLinks/worker-a"), {
      worker_uid: "worker-a",
      owner_uid: "owner-a",
      worker_type: "MAC_AGENT",
      scopes: ["generation"],
      pairing_hash: hash,
      revoked_at: null,
      created_at: serverTimestamp(),
      updated_at: serverTimestamp(),
      last_seen_at: null,
    });
    await assertSucceeds(batch.commit());
  });

  it("requires a recent counted attempt before reading a pairing request", async () => {
    const hash = "e".repeat(64);
    await seed(`pairingRequests/${hash}`, {
      code_hash: hash,
      worker_uid: "worker-a",
      owner_uid: null,
      used_at: null,
      attempt_count: 0,
      created_at: Timestamp.now(),
      expires_at: Timestamp.fromMillis(Date.now() + 5 * 60_000),
    });

    const database = ownerDb();
    await assertFails(getDoc(doc(database, `pairingRequests/${hash}`)));
    await assertSucceeds(setDoc(doc(database, "pairingAttempts/owner-a"), {
      owner_uid: "owner-a",
      attempt_count: 1,
      last_pairing_hash: hash,
      updated_at: serverTimestamp(),
    }));
    await assertSucceeds(getDoc(doc(database, `pairingRequests/${hash}`)));
  });

  it("rejects expired codes and a sixth pairing attempt", async () => {
    const expiredHash = "d".repeat(64);
    await seed(`pairingRequests/${expiredHash}`, {
      code_hash: expiredHash,
      worker_uid: "worker-a",
      owner_uid: null,
      used_at: null,
      attempt_count: 0,
      created_at: Timestamp.fromMillis(Date.now() - 120_000),
      expires_at: Timestamp.fromMillis(Date.now() - 60_000),
    });
    const database = ownerDb();
    await assertSucceeds(setDoc(doc(database, "pairingAttempts/owner-a"), {
      owner_uid: "owner-a",
      attempt_count: 1,
      last_pairing_hash: expiredHash,
      updated_at: serverTimestamp(),
    }));
    await assertFails(getDoc(doc(database, `pairingRequests/${expiredHash}`)));

    await seed("pairingAttempts/owner-a", {
      owner_uid: "owner-a",
      attempt_count: 5,
      last_pairing_hash: expiredHash,
      updated_at: Timestamp.now(),
    });
    await assertFails(updateDoc(doc(database, "pairingAttempts/owner-a"), {
      attempt_count: 6,
      last_pairing_hash: "f".repeat(64),
      updated_at: serverTimestamp(),
    }));
  });

  it("allows the five-attempt window to reset after ten minutes", async () => {
    await seed("pairingAttempts/owner-a", {
      owner_uid: "owner-a",
      attempt_count: 5,
      last_pairing_hash: "a".repeat(64),
      updated_at: Timestamp.fromMillis(Date.now() - 11 * 60_000),
    });
    await assertSucceeds(updateDoc(doc(ownerDb(), "pairingAttempts/owner-a"), {
      attempt_count: 1,
      last_pairing_hash: "b".repeat(64),
      updated_at: serverTimestamp(),
    }));
  });

  it("allows an owner to reconnect the same worker after revocation", async () => {
    const oldHash = "1".repeat(64);
    const newHash = "2".repeat(64);
    await seed("workerLinks/worker-a", {
      worker_uid: "worker-a",
      owner_uid: "owner-a",
      worker_type: "MAC_AGENT",
      scopes: ["generation"],
      pairing_hash: oldHash,
      revoked_at: Timestamp.now(),
      created_at: Timestamp.now(),
      updated_at: Timestamp.now(),
      last_seen_at: null,
    });
    await seed(`pairingRequests/${newHash}`, {
      code_hash: newHash,
      worker_uid: "worker-a",
      owner_uid: null,
      used_at: null,
      attempt_count: 0,
      created_at: Timestamp.now(),
      expires_at: Timestamp.fromMillis(Date.now() + 5 * 60_000),
    });

    const database = ownerDb();
    await assertSucceeds(setDoc(doc(database, "pairingAttempts/owner-a"), {
      owner_uid: "owner-a",
      attempt_count: 1,
      last_pairing_hash: newHash,
      updated_at: serverTimestamp(),
    }));
    const batch = writeBatch(database);
    batch.update(doc(database, `pairingRequests/${newHash}`), {
      owner_uid: "owner-a",
      used_at: serverTimestamp(),
      attempt_count: 1,
    });
    batch.set(doc(database, "workerLinks/worker-a"), {
      worker_uid: "worker-a",
      owner_uid: "owner-a",
      worker_type: "MAC_AGENT",
      scopes: ["generation"],
      pairing_hash: newHash,
      revoked_at: null,
      created_at: serverTimestamp(),
      updated_at: serverTimestamp(),
      last_seen_at: null,
    });
    await assertSucceeds(batch.commit());
  });
});

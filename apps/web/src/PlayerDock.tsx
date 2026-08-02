import type { ParsedBook } from "@audiobook/contracts";
import type { CSSProperties } from "react";
import { useEffect, useRef, useState } from "react";

import { loadPrivateAssetBytes, type AudioChunk } from "./cloud";
import {
  chunkForSegment,
  formatPlaybackTime,
  loadChunkTimeline,
  readyChunks,
  textSegmentById,
  timelineSegmentAt,
  type ChunkTimeline,
} from "./player";

export interface PlayerPosition {
  chapterId: string;
  segmentId: string;
  segmentOrder: number;
  audioOffsetSeconds: number;
}

export interface PlayerJumpRequest {
  key: number;
  segmentId: string;
  autoplay: boolean;
}

interface PlayerDockProps {
  book: ParsedBook;
  ownerUid: string;
  chunks: AudioChunk[];
  resumeSegmentId: string;
  resumeOffsetSeconds: number;
  jumpRequest: PlayerJumpRequest | null;
  macOnline: boolean;
  onHighlight: (chapterId: string, segmentId: string) => void;
  onPosition: (position: PlayerPosition, syncCloud: boolean) => void;
  onBookmark: (position: PlayerPosition) => void;
  onRepair: (chunk: AudioChunk) => void;
  onNotice: (message: string) => void;
}

export function PlayerDock({
  book,
  ownerUid,
  chunks,
  resumeSegmentId,
  resumeOffsetSeconds,
  jumpRequest,
  macOnline,
  onHighlight,
  onPosition,
  onBookmark,
  onRepair,
  onNotice,
}: PlayerDockProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const autoPlayNext = useRef(false);
  const handledJumpRequestKey = useRef<number | null>(null);
  const lastLocalSave = useRef(0);
  const resumeApplied = useRef("");
  const [activeChunkId, setActiveChunkId] = useState("");
  const [timeline, setTimeline] = useState<ChunkTimeline | null>(null);
  const [pendingSeek, setPendingSeek] = useState<{ segmentId: string; fallback: number; autoplay: boolean } | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [sleepMinutes, setSleepMinutes] = useState(0);
  const [playbackError, setPlaybackError] = useState("");
  const [audioSource, setAudioSource] = useState("");
  const [sourceLoading, setSourceLoading] = useState(false);
  const orderedChunks = readyChunks(book, chunks);
  const activeChunk = orderedChunks.find((chunk) => chunk.chunk_id === activeChunkId);

  useEffect(() => {
    const resumeKey = `${book.book_id}:${resumeSegmentId}`;
    if (resumeSegmentId && resumeApplied.current !== resumeKey) {
      resumeApplied.current = resumeKey;
      const resumed = chunkForSegment(book, chunks, resumeSegmentId);
      if (resumed) {
        setActiveChunkId(resumed.chunk_id);
        setPendingSeek({
          segmentId: resumeSegmentId,
          fallback: resumeOffsetSeconds,
          autoplay: false,
        });
      } else {
        setPendingSeek(null);
        const text = textSegmentById(book, resumeSegmentId);
        if (text) onHighlight(text.chapter_id, text.segment_id);
      }
      return;
    }
    const currentStillReady = orderedChunks.some((chunk) => chunk.chunk_id === activeChunkId);
    if (currentStillReady) return;
    const next = orderedChunks[0];
    setActiveChunkId(next?.chunk_id || "");
    if (next && !resumeSegmentId) {
      setPendingSeek({
        segmentId: next.start_segment_id,
        fallback: 0,
        autoplay: false,
      });
    }
  }, [activeChunkId, book, chunks, onHighlight, orderedChunks, resumeOffsetSeconds, resumeSegmentId]);

  useEffect(() => {
    if (!activeChunk) {
      setTimeline(null);
      return;
    }
    const controller = new AbortController();
    setTimeline(null);
    void loadChunkTimeline(ownerUid, activeChunk, controller.signal)
      .then((value) => setTimeline(value))
      .catch(() => setTimeline(null));
    return () => controller.abort();
  }, [activeChunk, ownerUid]);

  useEffect(() => {
    if (!activeChunk) {
      setAudioSource("");
      setSourceLoading(false);
      return;
    }
    if (
      activeChunk.storage_mode !== "PRIVATE_FIRESTORE"
      && activeChunk.storage_mode !== "LOCAL_MAC"
    ) {
      setAudioSource(activeChunk.asset_url || "");
      setSourceLoading(false);
      return;
    }
    if (
      activeChunk.storage_mode === "PRIVATE_FIRESTORE"
      && (!ownerUid || !activeChunk.private_audio_key)
    ) {
      setAudioSource("");
      setPlaybackError("这段私有音频缺少登录权限或文件索引。");
      setSourceLoading(false);
      return;
    }
    if (activeChunk.storage_mode === "LOCAL_MAC" && !activeChunk.asset_url) {
      setAudioSource("");
      setPlaybackError("这段本地音频缺少文件地址，请重新准备。");
      setSourceLoading(false);
      return;
    }
    const controller = new AbortController();
    let objectUrl = "";
    setAudioSource("");
    setSourceLoading(true);
    setPlaybackError("");
    const audioBlob = activeChunk.storage_mode === "LOCAL_MAC"
      ? fetch(activeChunk.asset_url!, {
          cache: "no-store",
          signal: controller.signal,
        }).then(async (response) => {
          if (!response.ok) throw new Error(`LOCAL_AUDIO_HTTP_${response.status}`);
          return response.blob();
        })
      : loadPrivateAssetBytes(
          ownerUid,
          activeChunk.private_audio_key!,
          activeChunk.sha256,
          controller.signal,
        ).then((bytes) => new Blob([bytes], { type: "audio/mp4" }));
    void audioBlob.then((blob) => {
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob);
      setAudioSource(objectUrl);
    }).catch((error: unknown) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setPlaybackError(
        activeChunk.storage_mode === "LOCAL_MAC"
          ? "这段本地音频暂时无法读取，请确认 Mac 已连接。"
          : "这段私有音频暂时无法读取，请确认已经登录。",
      );
    }).finally(() => {
      if (!controller.signal.aborted) setSourceLoading(false);
    });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [activeChunk, ownerUid]);

  useEffect(() => {
    if (!jumpRequest || handledJumpRequestKey.current === jumpRequest.key) return;
    handledJumpRequestKey.current = jumpRequest.key;
    const chunk = chunkForSegment(book, chunks, jumpRequest.segmentId);
    if (!chunk) {
      onNotice(macOnline ? "这一章的音频还在生成，请稍后再听。" : "这一章还没有音频，等待 Mac 开机后继续生成。");
      return;
    }
    setActiveChunkId(chunk.chunk_id);
    setPendingSeek({ segmentId: jumpRequest.segmentId, fallback: 0, autoplay: jumpRequest.autoplay });
  }, [book, chunks, jumpRequest, macOnline, onNotice]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !pendingSeek || audio.readyState === 0) return;
    const segment = timeline?.segments.find((item) => item.segment_id === pendingSeek.segmentId);
    audio.currentTime = segment?.start_seconds ?? pendingSeek.fallback;
    setCurrentTime(audio.currentTime);
    onHighlight(segment?.chapter_id || activeChunk?.chapter_id || "", pendingSeek.segmentId);
    if (pendingSeek.autoplay) void audio.play();
    setPendingSeek(null);
  }, [activeChunk, onHighlight, pendingSeek, timeline]);

  useEffect(() => {
    if (!sleepMinutes || !playing) return;
    const timer = window.setTimeout(() => {
      audioRef.current?.pause();
      setSleepMinutes(0);
      onNotice("睡眠定时已结束，播放已暂停。");
    }, sleepMinutes * 60 * 1000);
    return () => window.clearTimeout(timer);
  }, [onNotice, playing, sleepMinutes]);

  useEffect(() => {
    if (!("mediaSession" in navigator) || !("MediaMetadata" in window)) return;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: book.title,
      artist: book.author || "米兰读书",
      album: "米兰读书",
      artwork: [
        {
          src: `${window.location.origin}${import.meta.env.BASE_URL}app-icon-192.png`,
          sizes: "192x192",
          type: "image/png",
        },
        {
          src: `${window.location.origin}${import.meta.env.BASE_URL}app-icon-512.png`,
          sizes: "512x512",
          type: "image/png",
        },
      ],
    });
    const audio = audioRef.current;
    const seek = (seconds: number) => {
      if (audio) audio.currentTime = Math.max(0, Math.min(audio.duration || 0, audio.currentTime + seconds));
    };
    navigator.mediaSession.setActionHandler("play", () => void audio?.play());
    navigator.mediaSession.setActionHandler("pause", () => audio?.pause());
    navigator.mediaSession.setActionHandler("seekbackward", () => seek(-15));
    navigator.mediaSession.setActionHandler("seekforward", () => seek(15));
    navigator.mediaSession.setActionHandler("previoustrack", () => moveToChunk(-1, true));
    navigator.mediaSession.setActionHandler("nexttrack", () => moveToChunk(1, true));
    return () => {
      navigator.mediaSession.setActionHandler("play", null);
      navigator.mediaSession.setActionHandler("pause", null);
      navigator.mediaSession.setActionHandler("seekbackward", null);
      navigator.mediaSession.setActionHandler("seekforward", null);
      navigator.mediaSession.setActionHandler("previoustrack", null);
      navigator.mediaSession.setActionHandler("nexttrack", null);
    };
  }, [activeChunkId, book, chunks]);

  function position(): PlayerPosition | null {
    if (!activeChunk) return null;
    const timed = timelineSegmentAt(timeline, audioRef.current?.currentTime || 0);
    const segmentId = timed?.segment_id || activeChunk.start_segment_id;
    const text = textSegmentById(book, segmentId);
    return {
      chapterId: timed?.chapter_id || text?.chapter_id || activeChunk.chapter_id,
      segmentId,
      segmentOrder: text?.order || 0,
      audioOffsetSeconds: audioRef.current?.currentTime || 0,
    };
  }

  function savePosition(syncCloud: boolean) {
    const value = position();
    if (value) onPosition(value, syncCloud);
  }

  function updateTime() {
    const audio = audioRef.current;
    if (!audio) return;
    setCurrentTime(audio.currentTime);
    setDuration(audio.duration || activeChunk?.duration_seconds || 0);
    if ("mediaSession" in navigator && Number.isFinite(audio.duration) && audio.duration > 0) {
      navigator.mediaSession.setPositionState({
        duration: audio.duration,
        playbackRate: audio.playbackRate,
        position: Math.max(0, Math.min(audio.currentTime, audio.duration - 0.001)),
      });
    }
    const value = position();
    if (value) onHighlight(value.chapterId, value.segmentId);
    if (Date.now() - lastLocalSave.current >= 5000) {
      lastLocalSave.current = Date.now();
      savePosition(false);
    }
  }

  function move(seconds: number) {
    const audio = audioRef.current;
    if (!audio || !Number.isFinite(audio.duration)) return;
    audio.currentTime = Math.max(0, Math.min(audio.duration, audio.currentTime + seconds));
    updateTime();
  }

  function moveToChunk(offset: number, autoplay: boolean) {
    if (!activeChunk) return;
    const index = orderedChunks.findIndex((chunk) => chunk.chunk_id === activeChunk.chunk_id);
    const next = orderedChunks[index + offset];
    if (!next) {
      onNotice(offset > 0
        ? (macOnline ? "后面的音频仍在生成。" : "后面的音频要等 Mac 开机后继续生成。")
        : "已经是第一段音频。");
      return;
    }
    autoPlayNext.current = autoplay;
    setActiveChunkId(next.chunk_id);
    setPendingSeek({ segmentId: next.start_segment_id, fallback: 0, autoplay });
  }

  async function togglePlayback() {
    const audio = audioRef.current;
    if (!audio || !activeChunk || !audioSource) {
      if (sourceLoading) {
        onNotice("正在安全读取当前私有音频，请稍等一下。");
        return;
      }
      onNotice(macOnline ? "第一段音频还在生成，请稍后再听。" : "还没有可播放音频，等待 Mac 开机后继续生成。");
      return;
    }
    if (audio.paused) await audio.play();
    else audio.pause();
  }

  const progressPercent = duration > 0 ? Math.min(100, (currentTime / duration) * 100) : 0;
  const statusTitle = sourceLoading
    ? "正在读取私有音频"
    : activeChunk
    ? (playing ? "正在朗读" : "已暂停")
    : (macOnline ? "正在准备音频" : "等待 Mac 开机");

  return (
    <footer className="player-dock" aria-label="听书播放器">
      <audio
        ref={audioRef}
        src={audioSource || undefined}
        preload="metadata"
        onLoadedMetadata={() => {
          const audio = audioRef.current;
          if (!audio) return;
          audio.playbackRate = playbackRate;
          setDuration(audio.duration || activeChunk?.duration_seconds || 0);
          if (pendingSeek) {
            const segment = timeline?.segments.find((item) => item.segment_id === pendingSeek.segmentId);
            audio.currentTime = segment?.start_seconds ?? pendingSeek.fallback;
            if (pendingSeek.autoplay || autoPlayNext.current) void audio.play();
            autoPlayNext.current = false;
            setPendingSeek(null);
          }
        }}
        onTimeUpdate={updateTime}
        onPlay={() => { setPlaying(true); setPlaybackError(""); }}
        onPause={() => { setPlaying(false); savePosition(true); }}
        onEnded={() => { savePosition(true); moveToChunk(1, true); }}
        onError={() => setPlaybackError(navigator.onLine ? "这段音频无法读取。" : "网络已断开，已保留收听位置。")}
      />
      <button onClick={() => move(-15)} disabled={!activeChunk} aria-label="后退15秒">−15</button>
      <button className="play-button" onClick={() => void togglePlayback()} disabled={!activeChunk || sourceLoading} aria-label={playing ? "暂停" : "播放"}>{playing ? "Ⅱ" : "▶"}</button>
      <button onClick={() => move(15)} disabled={!activeChunk} aria-label="前进15秒">+15</button>
      <div className="player-status">
        <b>{statusTitle}</b>
        <span>{playbackError || (activeChunk ? `${book.title} · ${formatPlaybackTime(currentTime)}` : "已生成的音频不依赖 Mac 在线")}</span>
      </div>
      <label className="player-progress" aria-label="播放进度">
        <input
          type="range"
          min="0"
          max={duration || 0}
          step="0.1"
          value={Math.min(currentTime, duration || 0)}
          disabled={!activeChunk}
          onChange={(event) => {
            if (audioRef.current) audioRef.current.currentTime = Number(event.target.value);
            setCurrentTime(Number(event.target.value));
          }}
          onMouseUp={() => savePosition(true)}
          onTouchEnd={() => savePosition(true)}
          style={{ "--player-progress": `${progressPercent}%` } as CSSProperties}
        />
      </label>
      <span className="player-time">{formatPlaybackTime(currentTime)} / {formatPlaybackTime(duration)}</span>
      <div className="player-tools">
        <select
          aria-label="播放速度"
          value={playbackRate}
          onChange={(event) => {
            const rate = Number(event.target.value);
            setPlaybackRate(rate);
            if (audioRef.current) audioRef.current.playbackRate = rate;
          }}
        >
          {[0.75, 1, 1.25, 1.5, 2].map((rate) => <option key={rate} value={rate}>{rate}×</option>)}
        </select>
        <select aria-label="睡眠定时" value={sleepMinutes} onChange={(event) => setSleepMinutes(Number(event.target.value))}>
          <option value="0">不定时</option>
          <option value="15">15 分钟</option>
          <option value="30">30 分钟</option>
          <option value="60">60 分钟</option>
        </select>
        <button
          className="player-tool-button"
          disabled={!activeChunk}
          onClick={() => { const value = position(); if (value) onBookmark(value); }}
        >书签</button>
        {playbackError && navigator.onLine && activeChunk && (
          <button className="player-tool-button is-warning" onClick={() => onRepair(activeChunk)}>重新准备</button>
        )}
      </div>
    </footer>
  );
}

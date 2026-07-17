import { z } from "zod";

export const publicationModeSchema = z.enum(["LOCAL_ONLY", "PUBLIC_RIGHTS_CONFIRMED"]);
export const segmentKindSchema = z.enum(["HEADING", "PARAGRAPH", "FOOTNOTE"]);

export const textSegmentSchema = z.object({
  segment_id: z.string().uuid(),
  chapter_id: z.string().uuid(),
  order: z.number().int().nonnegative(),
  display_text: z.string().min(1),
  spoken_text: z.string(),
  text_hash: z.string().regex(/^[a-f0-9]{64}$/),
  kind: segmentKindSchema,
});

export const chapterSchema = z.object({
  chapter_id: z.string().uuid(),
  order: z.number().int().nonnegative(),
  title: z.string().min(1),
  source_href: z.string().min(1),
  segments: z.array(textSegmentSchema).min(1),
});

export const parsedBookSchema = z.object({
  book_id: z.string().uuid(),
  title: z.string().min(1),
  author: z.string(),
  source_format: z.enum(["EPUB", "TXT"]),
  source_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  publication_mode: publicationModeSchema,
  chapters: z.array(chapterSchema).min(1),
  warnings: z.array(z.string()),
  rights_confirmed_at: z.string().nullable(),
});

export type PublicationMode = z.infer<typeof publicationModeSchema>;
export type TextSegment = z.infer<typeof textSegmentSchema>;
export type Chapter = z.infer<typeof chapterSchema>;
export type ParsedBook = z.infer<typeof parsedBookSchema>;

export const generationStatusSchema = z.enum([
  "QUEUED",
  "LEASED",
  "GENERATING",
  "ENCODING",
  "UPLOADING",
  "READY",
  "PAUSED",
  "FAILED_RETRYABLE",
  "FAILED_FINAL",
  "DELETING",
  "DELETED",
  "CANCELLED",
]);

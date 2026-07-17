import type { ParsedBook } from "@audiobook/contracts";

const chapterOne = "f414f9e6-7bf2-5ae4-855d-bb7bd5bed123";
const chapterTwo = "1f86c46f-a572-5231-aa27-dcb99137cf56";

export const demoBook: ParsedBook = {
  book_id: "356fc83a-1b37-5571-bb94-9d168a6a7c2f",
  title: "山窗小札",
  author: "项目自制示例",
  source_format: "TXT",
  source_sha256: "c4d7c5d721561d14acfcf22f449f6939f6c43268fd8790a083a612a99b2211f0",
  publication_mode: "LOCAL_ONLY",
  warnings: [],
  rights_confirmed_at: null,
  chapters: [
    {
      chapter_id: chapterOne,
      order: 0,
      title: "第一章 清晨",
      source_href: "demo/chapter-1",
      segments: [
        {
          segment_id: "589a2d8d-e91d-5e87-a883-0396e551a92b",
          chapter_id: chapterOne,
          order: 0,
          display_text: "天色刚亮，窗纸先有了温度。院里的竹影轻轻移动，像书页边上一行未写完的批注。",
          spoken_text: "天色刚亮，窗纸先有了温度。院里的竹影轻轻移动，像书页边上一行未写完的批注。",
          text_hash: "5d9b37fb878ad3ea638a0e406f7906582be1e74e94634067db5b9685ae3e8117",
          kind: "PARAGRAPH",
        },
        {
          segment_id: "e339a9eb-eb7a-5fa8-a96a-743337b74a35",
          chapter_id: chapterOne,
          order: 1,
          display_text: "水在壶中渐响，我把昨日读到的地方重新翻开。",
          spoken_text: "水在壶中渐响，我把昨日读到的地方重新翻开。",
          text_hash: "2bd424cb292adf2e0fcd8ad585ba267ac4f85d84fa6fc77976e03ad40040ea80",
          kind: "PARAGRAPH",
        },
      ],
    },
    {
      chapter_id: chapterTwo,
      order: 1,
      title: "第二章 夜读",
      source_href: "demo/chapter-2",
      segments: [
        {
          segment_id: "1c34e102-ae19-55ab-9686-7fd4259f3590",
          chapter_id: chapterTwo,
          order: 0,
          display_text: "夜里没有风，灯下只听见纸张翻动。读得慢些，句子便有了自己的呼吸。",
          spoken_text: "夜里没有风，灯下只听见纸张翻动。读得慢些，句子便有了自己的呼吸。",
          text_hash: "1b9762170a1b022c780130a6916088422b42da1c012c4ca74bf0acec41d7aeb2",
          kind: "PARAGRAPH",
        },
      ],
    },
  ],
};

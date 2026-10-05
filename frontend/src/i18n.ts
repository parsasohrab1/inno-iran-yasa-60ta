export type Lang = "fa" | "en";

export const messages = {
  fa: {
    title: "سامانه بازرسی لاستیک ایران یاسا",
    total: "کل بازرسی‌ها",
    fpy: "FPY",
    defectRate: "نرخ عیب",
    review: "نیاز به بررسی",
    topDefects: "عیوب برتر",
    live: "بازرسی‌های زنده",
    part: "قطعه",
    line: "خط",
    status: "وضعیت",
    none: "بدون داده",
  },
  en: {
    title: "Iran Yasa Rubber Inspection",
    total: "Total inspections",
    fpy: "FPY",
    defectRate: "Defect rate",
    review: "Needs review",
    topDefects: "Top defects",
    live: "Live inspections",
    part: "Part",
    line: "Line",
    status: "Status",
    none: "No data",
  },
} as const;

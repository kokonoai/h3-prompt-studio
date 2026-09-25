import React, { createContext, useContext, useEffect, useMemo, useState } from "react";

export type UiLanguage = "zh-CN" | "zh-TW" | "en" | "ja";

export const UI_LANGUAGE_OPTIONS: [UiLanguage, string][] = [
  ["en", "English"],
  ["ja", "日本語"],
  ["zh-CN", "简体中文"],
  ["zh-TW", "繁體中文"],
];

type Translations = Partial<Record<UiLanguage, string>> & { "zh-CN": string };

export function localText(language: UiLanguage, values: Translations) {
  return values[language] || values.en || values["zh-CN"];
}

type UiLanguageContextValue = {
  language: UiLanguage;
  setLanguage: (language: UiLanguage) => void;
  text: (values: Translations) => string;
};

// English keeps isolated component renders backward-compatible in tests and
// embedders. The real application always supplies the persisted/browser locale.
const FALLBACK_CONTEXT: UiLanguageContextValue = {
  language: "en",
  setLanguage: () => {},
  text: values => localText("en", values),
};
const UiLanguageContext = createContext<UiLanguageContextValue>(FALLBACK_CONTEXT);

function savedLanguage(): UiLanguage {
  try {
    const value = localStorage.getItem("h3-ui-language");
    if (UI_LANGUAGE_OPTIONS.some(([code]) => code === value)) return value as UiLanguage;
    const browser = navigator.language.toLowerCase();
    if (browser.startsWith("ja")) return "ja";
    if (browser === "zh-tw" || browser === "zh-hk" || browser === "zh-mo") return "zh-TW";
    if (browser.startsWith("zh")) return "zh-CN";
  } catch {
    // Private browsing can disable storage; Simplified Chinese remains a safe default.
  }
  return "zh-CN";
}

export function UiLanguageProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguage] = useState<UiLanguage>(savedLanguage);
  useEffect(() => {
    document.documentElement.lang = language;
    try { localStorage.setItem("h3-ui-language", language); } catch { /* Optional storage. */ }
  }, [language]);
  const value = useMemo<UiLanguageContextValue>(() => ({
    language,
    setLanguage,
    text: values => localText(language, values),
  }), [language]);
  return <UiLanguageContext.Provider value={value}>{children}</UiLanguageContext.Provider>;
}

export function useUiLanguage() {
  return useContext(UiLanguageContext);
}

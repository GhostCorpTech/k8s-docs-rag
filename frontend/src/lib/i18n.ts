// Strings for the public demo screens (landing, key entry, read-only mode).
// The chat UI itself stays in English.
export type Lang = "en" | "ru";

const LANG = "k8s-rag.lang";

export function loadLang(): Lang {
  try {
    return localStorage.getItem(LANG) === "ru" ? "ru" : "en";
  } catch {
    return "en";
  }
}

export function saveLang(lang: Lang): void {
  try {
    localStorage.setItem(LANG, lang);
  } catch {
    /* ignore */
  }
}

function ruPlural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

export const STRINGS = {
  en: {
    badge: "Live demo · access by key",
    title: "Ask the Kubernetes documentation in English or Russian",
    lead: "The assistant answers from the official kubernetes.io documentation. Hybrid search finds the relevant sections, and every answer cites them.",
    facts: [
      ["1,730", "kubernetes.io pages indexed"],
      ["0.96", "Recall@5 on a 50-question benchmark"],
      ["0 of 20", "made-up answers to questions the docs do not cover"],
    ],
    accessTitle: "Open the demo",
    accessText:
      "The live chat runs on a paid LLM API, so it opens with a personal access key. Keys are free: write to me and I will send you one.",
    keyLabel: "Access key",
    submit: "Continue",
    checking: "Checking…",
    terms: (days: number, limit: number) => `A key is valid for ${days} days and allows ${limit} questions a day.`,
    requestTitle: "Request a key",
    mailSubject: "Demo key: Kubernetes Docs Assistant",
    exploreTitle: "Look around without a key",
    sampleTitle: "Recorded answer",
    sampleText: "A real answer with sources and retrieval details, recorded on the live stack.",
    evalTitle: "Evaluation results",
    evalText: "Benchmark metrics and per-question ranks for four configurations.",
    codeTitle: "Source code",
    codeText: "Architecture, ADRs, tests and the evaluation harness on GitHub.",
    errors: {
      invalid_key: "This key is not valid. Check that it was copied in full.",
      key_required: "Enter an access key.",
      key_revoked: "This key has been revoked. Write to me if you need a new one.",
      key_expired: "This key has expired. Write to me and I will extend it.",
      too_many_attempts: "Too many invalid attempts. Try again in a few minutes.",
      network: "The demo server is not responding. Please try again later.",
    } as Record<string, string>,
    lockedBar: "This is a recorded example. To ask your own questions, you need an access key.",
    enterKey: "Enter access key",
    sampleLabel: "Example",
    footer: "© 2026 GhostCorp · AI solutions for business processes",
  },
  ru: {
    badge: "Живое демо · доступ по ключу",
    title: "Спросите документацию Kubernetes на русском или английском",
    lead: "Ассистент отвечает по официальной документации kubernetes.io. Гибридный поиск находит нужные разделы, и каждый ответ на них ссылается.",
    facts: [
      ["1 730", "страниц kubernetes.io в индексе"],
      ["0,96", "Recall@5 на бенчмарке из 50 вопросов"],
      ["0 из 20", "выдуманных ответов на вопросы, которых нет в документации"],
    ],
    accessTitle: "Открыть демо",
    accessText:
      "Живой чат работает на платном LLM API, поэтому открывается по личному ключу. Ключ бесплатный: напишите мне, и я его пришлю.",
    keyLabel: "Ключ доступа",
    submit: "Войти",
    checking: "Проверяю…",
    terms: (days: number, limit: number) =>
      `Ключ действует ${days} ${ruPlural(days, "день", "дня", "дней")}, до ${limit} ${ruPlural(limit, "вопроса", "вопросов", "вопросов")} в день.`,
    requestTitle: "Получить ключ",
    mailSubject: "Ключ для демо: Kubernetes Docs Assistant",
    exploreTitle: "Посмотреть без ключа",
    sampleTitle: "Записанный ответ",
    sampleText: "Настоящий ответ с источниками и деталями поиска, записанный на рабочем стенде.",
    evalTitle: "Результаты оценки",
    evalText: "Метрики на бенчмарке и ранги по каждому вопросу для четырёх конфигураций.",
    codeTitle: "Исходный код",
    codeText: "Архитектура, ADR, тесты и стенд оценки на GitHub.",
    errors: {
      invalid_key: "Ключ не подошёл. Проверьте, что он скопирован целиком.",
      key_required: "Введите ключ доступа.",
      key_revoked: "Этот ключ отозван. Напишите мне, если нужен новый.",
      key_expired: "Срок действия ключа истёк. Напишите мне, и я его продлю.",
      too_many_attempts: "Слишком много неверных попыток. Попробуйте через несколько минут.",
      network: "Сервер демо не отвечает. Попробуйте позже.",
    } as Record<string, string>,
    lockedBar: "Это записанный пример. Чтобы задать свой вопрос, нужен ключ доступа.",
    enterKey: "Ввести ключ",
    sampleLabel: "Пример",
    footer: "© 2026 GhostCorp · AI-решения для бизнес-процессов",
  },
};

export type Strings = (typeof STRINGS)["en"];

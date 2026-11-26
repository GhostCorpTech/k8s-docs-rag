import { useState } from "react";
import { ArrowRight, BarChart3, GitBranch, KeyRound, Mail, MessageSquareText, Send } from "lucide-react";
import type { Lang, Strings } from "../lib/i18n";
import type { DemoInfo } from "../types";
import { Brand } from "./Sidebar";

function telegramHandle(url: string): string {
  const handle = url.replace(/^https?:\/\/(t\.me|telegram\.me)\//, "").replace(/\/$/, "");
  return handle.startsWith("@") ? handle : `@${handle}`;
}

export function LangSwitch({ lang, onLang }: { lang: Lang; onLang: (lang: Lang) => void }) {
  return (
    <div className="lang-switch" role="radiogroup" aria-label="Language">
      {(["en", "ru"] as const).map((value) => (
        <button
          key={value}
          role="radio"
          aria-checked={lang === value}
          className={lang === value ? "on" : ""}
          onClick={() => onLang(value)}
        >
          {value.toUpperCase()}
        </button>
      ))}
    </div>
  );
}

export function Landing({
  demo,
  t,
  lang,
  onLang,
  reason,
  onUnlock,
  onSample,
  onEval,
}: {
  demo: DemoInfo;
  t: Strings;
  lang: Lang;
  onLang: (lang: Lang) => void;
  /** Why the visitor is here again: an error code from a stored key that stopped working. */
  reason: string | null;
  onUnlock: (key: string) => Promise<string | null>;
  onSample: () => void;
  onEval: () => void;
}) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(reason);
  const { email, telegram } = demo.contact;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!key.trim() || busy) return;
    setBusy(true);
    setError(await onUnlock(key));
    setBusy(false);
  }

  return (
    <div className="landing">
      <header className="landing-top">
        <Brand />
        <LangSwitch lang={lang} onLang={onLang} />
      </header>

      <main className="landing-main">
        <section className="landing-hero">
          <span className="pill">
            <span className="dot ok" /> {t.badge}
          </span>
          <h1>{t.title}</h1>
          <p className="lead">{t.lead}</p>
          <dl className="facts">
            {t.facts.map(([value, label]) => (
              <div key={label}>
                <dt>{value}</dt>
                <dd>{label}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section className="landing-grid">
          <div className="card access-card">
            <h2>
              <KeyRound size={17} /> {t.accessTitle}
            </h2>
            <p>{t.accessText}</p>
            <form className="key-form" onSubmit={submit}>
              <label className="sr-only" htmlFor="access-key">
                {t.keyLabel}
              </label>
              <input
                id="access-key"
                value={key}
                onChange={(event) => setKey(event.target.value)}
                placeholder={`${t.keyLabel}: k8s_…`}
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? "key-error" : undefined}
              />
              <button type="submit" className="primary-button" disabled={!key.trim() || busy}>
                {busy ? t.checking : t.submit}
              </button>
            </form>
            {error && (
              <p className="key-error" id="key-error" role="alert">
                {t.errors[error] ?? error}
              </p>
            )}
            <p className="muted small">{t.terms(demo.key_days, demo.key_daily_limit)}</p>

            {(email || telegram) && (
              <div className="contacts">
                <h3>{t.requestTitle}</h3>
                {email && (
                  <a className="contact" href={`mailto:${email}?subject=${encodeURIComponent(t.mailSubject)}`}>
                    <Mail size={16} /> {email}
                  </a>
                )}
                {telegram && (
                  <a className="contact" href={telegram} target="_blank" rel="noreferrer">
                    <Send size={16} /> {telegramHandle(telegram)}
                  </a>
                )}
              </div>
            )}
          </div>

          <div className="card explore-card">
            <h2>{t.exploreTitle}</h2>
            <button className="explore-item" onClick={onSample}>
              <MessageSquareText size={18} />
              <span>
                <strong>{t.sampleTitle}</strong>
                <span>{t.sampleText}</span>
              </span>
              <ArrowRight size={16} className="explore-arrow" />
            </button>
            <button className="explore-item" onClick={onEval}>
              <BarChart3 size={18} />
              <span>
                <strong>{t.evalTitle}</strong>
                <span>{t.evalText}</span>
              </span>
              <ArrowRight size={16} className="explore-arrow" />
            </button>
            {demo.repository_url && (
              <a className="explore-item" href={demo.repository_url} target="_blank" rel="noreferrer">
                <GitBranch size={18} />
                <span>
                  <strong>{t.codeTitle}</strong>
                  <span>{t.codeText}</span>
                </span>
                <ArrowRight size={16} className="explore-arrow" />
              </a>
            )}
          </div>
        </section>

        <figure className="landing-shot" aria-hidden>
          <img className="only-light" src="/demo/chat-light.png" alt="" loading="lazy" width={1440} height={900} />
          <img className="only-dark" src="/demo/chat-dark.png" alt="" loading="lazy" width={1440} height={900} />
        </figure>
      </main>

      <footer className="landing-foot" lang={lang}>
        {t.footer}
      </footer>
    </div>
  );
}

// apps/site/src/components/ContactForm.tsx
//
// React island (client:load) rendered from contact.astro. Inserts leads
// directly via the anon Supabase client (public-insert RLS on `leads`,
// mirroring the pattern already used in src/lib/supabase.ts for pricing
// reads) — no BFF route involved.
//
// `source` distinguishes where a lead entered the funnel (this component is
// reused as-is if a second entry point ever needs it) and defaults to
// "contact" to match the brief's `contact.astro` usage.
import { useState } from "react";
import type { FormEvent } from "react";
import { supabase } from "../lib/supabase";

type Status = "idle" | "submitting" | "sent" | "error";

export function ContactForm({ source = "contact" }: { source?: string }) {
  const [status, setStatus] = useState<Status>("idle");
  const [hp, setHp] = useState(""); // honeypot — real users never see/fill this field
  const [form, setForm] = useState({ name: "", email: "", company: "", message: "" });

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (hp) return; // bot filled the honeypot → drop silently, no request, no state change

    if (!supabase) {
      // Build/deploy without PUBLIC_SUPABASE_URL/PUBLIC_SUPABASE_ANON_KEY set —
      // fail loudly to the visitor rather than silently claiming success.
      setStatus("error");
      return;
    }

    setStatus("submitting");
    const { error } = await supabase.from("leads").insert({ ...form, source });
    if (error) {
      setStatus("error");
      return;
    }
    setStatus("sent");
  }

  if (status === "sent") {
    return <p role="status">Thank you — we'll be in touch shortly.</p>;
  }

  return (
    <form onSubmit={submit} className="space-y-3 max-w-md">
      <label className="sr-only" htmlFor="hp-company-website">
        Company website
      </label>
      <input
        id="hp-company-website"
        aria-label="company website"
        tabIndex={-1}
        autoComplete="off"
        className="hidden"
        value={hp}
        onChange={(e) => setHp(e.target.value)}
      />
      <input
        aria-label="name"
        placeholder="Name"
        className="w-full border rounded p-2"
        value={form.name}
        onChange={(e) => setForm({ ...form, name: e.target.value })}
      />
      <input
        aria-label="email"
        placeholder="Email"
        type="email"
        required
        className="w-full border rounded p-2"
        value={form.email}
        onChange={(e) => setForm({ ...form, email: e.target.value })}
      />
      <input
        aria-label="company"
        placeholder="Company"
        className="w-full border rounded p-2"
        value={form.company}
        onChange={(e) => setForm({ ...form, company: e.target.value })}
      />
      <textarea
        aria-label="message"
        placeholder="How can we help?"
        required
        className="w-full border rounded p-2"
        value={form.message}
        onChange={(e) => setForm({ ...form, message: e.target.value })}
      />
      <button
        type="submit"
        disabled={status === "submitting"}
        className="px-4 py-2 rounded bg-primary text-primary-foreground disabled:opacity-50"
      >
        {status === "submitting" ? "Sending…" : "Book a demo"}
      </button>
      {status === "error" && (
        <p role="alert" className="text-sm text-destructive">
          Something went wrong sending your message — please try again, or email us directly at{" "}
          <a href="mailto:hello@aeronta.example" className="underline">
            hello@aeronta.example
          </a>
          .
        </p>
      )}
    </form>
  );
}

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ContactForm } from "./ContactForm";

const insert = vi.fn().mockResolvedValue({ error: null });
vi.mock("../lib/supabase", () => ({ supabase: { from: () => ({ insert }) } }));

describe("ContactForm", () => {
  it("submits name/email/message to leads and shows a thank-you", async () => {
    render(<ContactForm />);
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "a@b.co" } });
    fireEvent.change(screen.getByLabelText(/message/i), { target: { value: "demo please" } });
    fireEvent.click(screen.getByRole("button", { name: /send|book/i }));
    await waitFor(() => expect(insert).toHaveBeenCalled());
    expect(insert.mock.calls[0][0]).toMatchObject({ email: "a@b.co", source: "contact" });
    expect(await screen.findByText(/thank/i)).toBeInTheDocument();
  });

  it("does not submit when the honeypot is filled (bot)", async () => {
    insert.mockClear();
    render(<ContactForm />);
    // The honeypot input is visually hidden; a bot fills it.
    fireEvent.change(screen.getByLabelText(/company website/i), { target: { value: "spam" } });
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "a@b.co" } });
    fireEvent.change(screen.getByLabelText(/message/i), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: /send|book/i }));
    await new Promise((r) => setTimeout(r, 10));
    expect(insert).not.toHaveBeenCalled();
  });

  it("falls back to a plain retry-later message (no email) when PUBLIC_CONTACT_EMAIL is unset", async () => {
    vi.resetModules();
    vi.doMock("../lib/supabase", () => ({ supabase: null }));
    const { ContactForm: ContactFormNoSupabase } = await import("./ContactForm");

    render(<ContactFormNoSupabase />);
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "a@b.co" } });
    fireEvent.change(screen.getByLabelText(/message/i), { target: { value: "demo please" } });
    fireEvent.click(screen.getByRole("button", { name: /send|book/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/something went wrong/i);
    expect(alert).toHaveTextContent(/try again later/i);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByText(/thank/i)).not.toBeInTheDocument();
  });

  it("offers a mailto fallback when PUBLIC_CONTACT_EMAIL is configured", async () => {
    vi.resetModules();
    vi.stubEnv("PUBLIC_CONTACT_EMAIL", "hello@realaddress.com");
    vi.doMock("../lib/supabase", () => ({ supabase: null }));
    const { ContactForm: ContactFormWithEmail } = await import("./ContactForm");

    render(<ContactFormWithEmail />);
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "a@b.co" } });
    fireEvent.change(screen.getByLabelText(/message/i), { target: { value: "demo please" } });
    fireEvent.click(screen.getByRole("button", { name: /send|book/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/something went wrong/i);
    const link = screen.getByRole("link", { name: /hello@realaddress\.com/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "mailto:hello@realaddress.com");
    expect(screen.queryByText(/thank/i)).not.toBeInTheDocument();
    vi.unstubAllEnvs();
  });

  it("shows the retry/error message (and no thank-you) when the insert itself errors", async () => {
    vi.resetModules();
    const failingInsert = vi.fn().mockResolvedValue({ error: { message: "x" } });
    vi.doMock("../lib/supabase", () => ({ supabase: { from: () => ({ insert: failingInsert }) } }));
    const { ContactForm: ContactFormInsertError } = await import("./ContactForm");

    render(<ContactFormInsertError />);
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "a@b.co" } });
    fireEvent.change(screen.getByLabelText(/message/i), { target: { value: "demo please" } });
    fireEvent.click(screen.getByRole("button", { name: /send|book/i }));

    await waitFor(() => expect(failingInsert).toHaveBeenCalled());
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/something went wrong/i);
    expect(screen.queryByText(/thank/i)).not.toBeInTheDocument();
  });
});

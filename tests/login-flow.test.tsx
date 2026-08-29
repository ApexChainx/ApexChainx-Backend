/** ApexChain Network Operations Intelligence Platform */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LoginForm from "@/components/auth/LoginForm";

const mockPost = vi.fn();
const mockCompleteTwoFactorLogin = vi.fn();
const mockStoreSession = vi.fn();
const mockPush = vi.fn();
const mockRefresh = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
    refresh: mockRefresh,
  }),
}));

vi.mock("@/lib/api", () => ({
  api: {
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

vi.mock("@/hooks/useSession", () => ({
  useSession: () => ({
    storeSession: mockStoreSession,
  }),
}));

vi.mock("@/services/twoFactorService", () => ({
  completeTwoFactorLogin: (...args: unknown[]) => mockCompleteTwoFactorLogin(...args),
}));

const fullSession = {
  access_token: "acctok",
  refresh_token: "reftok",
  token_type: "bearer",
  expires_in: 3600,
  user: { id: "u1", email: "op@example.com", role: "engineer" },
};

describe("LoginForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockPost.mockReset();
    mockCompleteTwoFactorLogin.mockReset();
    mockStoreSession.mockReset();
    mockPush.mockReset();
    mockRefresh.mockReset();
  });

  async function submitCredentials() {
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "op@example.com");
    await user.type(screen.getByLabelText("Password"), "secure123");
    await user.click(screen.getByRole("button", { name: /^Sign in$/ }));
  }

  describe("2FA challenge branch", () => {
    it("advances to the challenge step when the login response requires a second factor", async () => {
      mockPost.mockResolvedValue({ data: { two_factor_required: true } });

      render(<LoginForm />);
      await submitCredentials();

      await waitFor(() => {
        expect(
          screen.getByRole("heading", { name: /two-factor authentication/i })
        ).toBeInTheDocument();
      });

      expect(screen.getByLabelText("Authentication code")).toBeInTheDocument();
      // We have NOT redirected or stored a session at the credentials step.
      expect(mockPush).not.toHaveBeenCalled();
      expect(mockStoreSession).not.toHaveBeenCalled();
    });

    it("advances to the challenge step when the backend rejects with a 2FA-required signal", async () => {
      const err = Object.assign(new Error("Second factor required"), {
        response: {
          status: 401,
          data: { detail: "Second factor required" },
        },
      });
      mockPost.mockRejectedValue(err);

      render(<LoginForm />);
      await submitCredentials();

      await waitFor(() => {
        expect(
          screen.getByRole("heading", { name: /two-factor authentication/i })
        ).toBeInTheDocument();
      });
    });

    it("stores the session and redirects home after a successful challenge", async () => {
      mockPost.mockResolvedValue({ data: { two_factor_required: true } });
      mockCompleteTwoFactorLogin.mockResolvedValue(fullSession);

      render(<LoginForm />);
      await submitCredentials();

      await userEvent.type(
        await screen.findByLabelText("Authentication code"),
        "123456"
      );
      await userEvent.click(screen.getByRole("button", { name: /^Verify and sign in$/ }));

      await waitFor(() => {
        expect(mockCompleteTwoFactorLogin).toHaveBeenCalledWith("123456");
        expect(mockStoreSession).toHaveBeenCalledWith("acctok", "reftok", {
          id: "u1",
          email: "op@example.com",
          role: "engineer",
        });
        expect(mockPush).toHaveBeenCalledWith("/");
      });
    });
  });

  describe("throttle-triggered disablement", () => {
    it("disables the challenge submit after repeated failed attempts with visible feedback", async () => {
      mockPost.mockResolvedValue({ data: { two_factor_required: true } });
      mockCompleteTwoFactorLogin.mockRejectedValue(new Error("Invalid code"));

      render(<LoginForm />);
      await submitCredentials();

      for (let i = 0; i < 5; i += 1) {
        const input = screen.getByLabelText("Authentication code") as HTMLInputElement;
        await userEvent.type(input, "000000");
        await userEvent.click(screen.getByRole("button", { name: /^Verify and sign in$/ }));

        if (i < 4) {
          await waitFor(() => {
            expect(screen.getByRole("button", { name: /^Verify and sign in$/ })).toBeEnabled();
          });
          await userEvent.clear(screen.getByLabelText("Authentication code"));
        }
      }

      await waitFor(() => {
        expect(
          screen.getByRole("button", { name: /verification locked/i })
        ).toBeDisabled();
      });

      expect(screen.getByText(/too many failed attempts/i)).toBeInTheDocument();
    });
  });
});
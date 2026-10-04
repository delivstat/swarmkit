import { ownerConfig } from "@/lib/auth-env";
import { verifyPassword } from "@/lib/password";
import { COOKIE_NAME, cookieAttributes, signSession } from "@/lib/session";
import { NextResponse } from "next/server";

interface Body {
	username?: string;
	password?: string;
}

export async function POST(req: Request): Promise<Response> {
	const cfg = ownerConfig();
	if (!cfg) {
		return NextResponse.json(
			{
				error: "server not configured — DOTS_OWNER_USERNAME / DOTS_OWNER_PASSWORD_HASH missing",
			},
			{ status: 503 },
		);
	}
	let body: Body;
	try {
		body = (await req.json()) as Body;
	} catch {
		return NextResponse.json({ error: "invalid_json" }, { status: 400 });
	}
	const username = body.username?.trim();
	const password = body.password ?? "";
	if (!username || !password) {
		return NextResponse.json({ error: "username_and_password_required" }, { status: 400 });
	}
	if (username !== cfg.username || !verifyPassword(password, cfg.passwordHash)) {
		return NextResponse.json({ error: "invalid_credentials" }, { status: 401 });
	}
	const { value, maxAge } = await signSession(username);
	const secure = new URL(req.url).protocol === "https:";
	const res = NextResponse.json({ owner: username });
	res.headers.append("Set-Cookie", `${COOKIE_NAME}=${value}; ${cookieAttributes(secure, maxAge)}`);
	return res;
}

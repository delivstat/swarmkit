import { COOKIE_NAME, verifySession } from "@/lib/session";
import { type NextRequest, NextResponse } from "next/server";

// Runs in the Edge runtime. session.ts uses Web Crypto (SubtleCrypto), which works in both Edge
// and Node — so this file and the API routes share one verifier.
export async function middleware(req: NextRequest): Promise<NextResponse> {
	const { pathname, search } = req.nextUrl;
	const isLogin = pathname === "/login";
	const isAuthApi = pathname.startsWith("/api/auth/");
	if (isLogin || isAuthApi) return NextResponse.next();
	const cookie = req.cookies.get(COOKIE_NAME)?.value;
	if (await verifySession(cookie)) return NextResponse.next();
	if (pathname.startsWith("/api/")) {
		return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
	}
	const url = req.nextUrl.clone();
	url.pathname = "/login";
	url.search = `?next=${encodeURIComponent(pathname + search)}`;
	return NextResponse.redirect(url);
}

export const config = {
	matcher: ["/((?!_next/|favicon.ico|robots.txt).*)"],
};

import { COOKIE_NAME, revoke, verifySession } from "@/lib/session";
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

export async function POST(): Promise<Response> {
	const store = await cookies();
	const cookie = store.get(COOKIE_NAME)?.value;
	const payload = await verifySession(cookie);
	if (payload) revoke(payload.jti, payload.exp);
	const res = NextResponse.json({ ok: true });
	res.headers.append("Set-Cookie", `${COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0`);
	return res;
}

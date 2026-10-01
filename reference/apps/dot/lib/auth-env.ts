// Env accessors for the owner credential. Kept in one place so the login route and the tests
// exercise identical resolution.

export interface OwnerConfig {
	username: string;
	passwordHash: string;
}

export function ownerConfig(): OwnerConfig | null {
	const username = process.env.DOT_OWNER_USERNAME;
	const passwordHash = process.env.DOT_OWNER_PASSWORD_HASH;
	if (!username || !passwordHash) return null;
	return { username, passwordHash };
}

/**
 * Pure helpers for the schema-driven designer: walk a JSON Schema so a form can render every field
 * from it (types, enums, constraints) with the schema `description` as the tooltip. Kept separate
 * from the React component so the schema-walking is unit-testable.
 */

// A JSON Schema node. Loose by nature — schemas are open-ended documents.
export type JsonSchema = Record<string, unknown>;

/** Resolve a local `$ref` (`#/$defs/X` or `#/definitions/X`) against the root schema. A node with
 * no `$ref` is returned unchanged; sibling keys on the `$ref` node (e.g. an overriding
 * `description`) win over the target. Non-local / unresolvable refs are returned as-is. */
export function resolveRef(root: JsonSchema, node: JsonSchema): JsonSchema {
	let current = node;
	const seen = new Set<string>();
	while (
		current &&
		typeof current === "object" &&
		typeof current.$ref === "string"
	) {
		const ref = current.$ref;
		if (seen.has(ref)) break;
		seen.add(ref);
		const match = ref.match(/^#\/(\$defs|definitions)\/(.+)$/);
		const bucketName = match?.[1];
		const key = match?.[2];
		if (!bucketName || !key) break;
		const bucket = root[bucketName] as Record<string, JsonSchema> | undefined;
		const target = bucket?.[key];
		if (!target) break;
		const { $ref: _drop, ...siblings } = current;
		current = { ...target, ...siblings };
	}
	return current;
}

export type FieldKind =
	| "const"
	| "enum"
	| "boolean"
	| "number"
	| "string"
	| "text"
	| "array"
	| "object"
	| "json";

/** Long free-text fields get a textarea instead of a single-line input. */
const TEXT_FIELDS = new Set([
	"prompt",
	"description",
	"instructions",
	"system_prompt",
]);

/** Classify a (resolved) schema node into a render kind. `name` lets us pick a textarea for known
 * long-text fields. Unknown shapes (oneOf/anyOf/etc.) fall back to a raw-JSON editor. */
export function fieldKind(schema: JsonSchema, name = ""): FieldKind {
	if (schema.const !== undefined) return "const";
	if (Array.isArray(schema.enum)) return "enum";
	const type = schema.type;
	if (type === "boolean") return "boolean";
	if (type === "integer" || type === "number") return "number";
	if (type === "array") return "array";
	if (type === "object" || schema.properties) return "object";
	if (type === "string") return TEXT_FIELDS.has(name) ? "text" : "string";
	return "json";
}

/** The artifact type a field references (via the canonical schema's `x-swarmkit-ref` hint), or
 * null. Drives workspace-populated pickers (dropdown / multi-select chips) instead of free-text id
 * entry. */
export function refType(schema: JsonSchema): string | null {
	const ref = schema["x-swarmkit-ref"];
	return typeof ref === "string" ? ref : null;
}

/** Options for reference pickers: artifact type → available ids in the workspace. */
export type RefOptions = Record<string, string[]>;

export interface FieldSpec {
	name: string;
	schema: JsonSchema;
	required: boolean;
}

/** Ordered (name, resolved-schema, required) for an object schema's properties. */
export function objectFields(
	root: JsonSchema,
	schema: JsonSchema,
): FieldSpec[] {
	const props = (schema.properties ?? {}) as Record<string, JsonSchema>;
	const required = new Set((schema.required as string[] | undefined) ?? []);
	return Object.keys(props).map((name) => ({
		name,
		schema: resolveRef(root, props[name] ?? {}),
		required: required.has(name),
	}));
}

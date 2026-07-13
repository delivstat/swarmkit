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
	| "oneof"
	| "boolean"
	| "number"
	| "string"
	| "text"
	| "array"
	| "map"
	| "object"
	| "json";

/** Merge an `allOf` node into one synthetic object — union of members' `properties` + `required`,
 * carrying `additionalProperties`/`type`. Members are resolved first. A node without `allOf` is
 * returned unchanged. Lets a schema like the `agent` def (base fields + children) render as a form. */
export function mergeAllOf(root: JsonSchema, schema: JsonSchema): JsonSchema {
	if (!Array.isArray(schema.allOf)) return schema;
	const props: Record<string, JsonSchema> = {
		...((schema.properties as Record<string, JsonSchema>) ?? {}),
	};
	const required = new Set<string>(
		(schema.required as string[] | undefined) ?? [],
	);
	let additionalProperties = schema.additionalProperties;
	let type = schema.type;
	for (const member of schema.allOf as JsonSchema[]) {
		const m = resolveRef(root, member);
		Object.assign(props, (m.properties as Record<string, JsonSchema>) ?? {});
		for (const r of (m.required as string[] | undefined) ?? []) required.add(r);
		if (m.additionalProperties !== undefined)
			additionalProperties = m.additionalProperties;
		if (m.type) type = m.type;
	}
	const { allOf: _allOf, ...rest } = schema;
	return {
		...rest,
		type: type ?? "object",
		properties: props,
		required: [...required],
		...(additionalProperties !== undefined ? { additionalProperties } : {}),
	};
}

/** Resolve a `$ref` then merge any `allOf`. Use wherever a field's schema is consumed. */
export function normalizeSchema(
	root: JsonSchema,
	node: JsonSchema,
): JsonSchema {
	const resolved = resolveRef(root, node);
	return Array.isArray(resolved.allOf) ? mergeAllOf(root, resolved) : resolved;
}

/** The value schema of a free-form map — an `object` with `additionalProperties` and no fixed
 * `properties` (opaque `config`, model `options`, a keyed artifact list) — or null. `true` → an open
 * value (`{}`). Objects with fixed properties (even if they also allow extra keys) are not maps. */
export function mapValueSchema(schema: JsonSchema): JsonSchema | null {
	const ap = schema.additionalProperties;
	if (!ap) return null;
	const hasProps =
		schema.properties && Object.keys(schema.properties as object).length > 0;
	if (hasProps) return null;
	if (schema.type === "object" || schema.type === undefined) {
		return ap === true ? {} : (ap as JsonSchema);
	}
	return null;
}

export interface Variant {
	label: string;
	schema: JsonSchema;
	/** The property + const value that identifies this variant (e.g. `type: "mcp_tool"`), if any. */
	discriminatorKey?: string;
	discriminatorValue?: string;
}

/** The `oneOf`/`anyOf` variants (each resolved + merged), with a discriminator taken from a `const`
 * `type`/`kind` property (else the title, else the index), or null when the node isn't a union. */
export function variants(
	root: JsonSchema,
	schema: JsonSchema,
): Variant[] | null {
	const raw = (schema.oneOf ?? schema.anyOf) as JsonSchema[] | undefined;
	if (!Array.isArray(raw)) return null;
	return raw.map((v, i) => {
		const rv = normalizeSchema(root, v);
		const props = (rv.properties ?? {}) as Record<string, JsonSchema>;
		for (const key of ["type", "kind"]) {
			const c = props[key]?.const;
			if (c !== undefined) {
				return {
					label: String(c),
					schema: rv,
					discriminatorKey: key,
					discriminatorValue: String(c),
				};
			}
		}
		const title = typeof rv.title === "string" ? rv.title : `option ${i + 1}`;
		return { label: title, schema: rv };
	});
}

/** Pick the variant matching a value's discriminator, else the first. */
export function activeVariant(vs: Variant[], value: unknown): number {
	if (value && typeof value === "object") {
		const obj = value as Record<string, unknown>;
		const i = vs.findIndex(
			(v) =>
				v.discriminatorKey && obj[v.discriminatorKey] === v.discriminatorValue,
		);
		if (i >= 0) return i;
	}
	return 0;
}

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
	if (
		(Array.isArray(schema.oneOf) && schema.oneOf.length > 0) ||
		(Array.isArray(schema.anyOf) && schema.anyOf.length > 0)
	)
		return "oneof";
	const type = schema.type;
	if (type === "boolean") return "boolean";
	if (type === "integer" || type === "number") return "number";
	if (type === "array") return "array";
	if (mapValueSchema(schema)) return "map";
	if (type === "object" || schema.properties || Array.isArray(schema.allOf))
		return "object";
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
	const merged = Array.isArray(schema.allOf)
		? mergeAllOf(root, schema)
		: schema;
	const props = (merged.properties ?? {}) as Record<string, JsonSchema>;
	const required = new Set((merged.required as string[] | undefined) ?? []);
	return Object.keys(props).map((name) => ({
		name,
		schema: normalizeSchema(root, props[name] ?? {}),
		required: required.has(name),
	}));
}

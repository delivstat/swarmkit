"use client";

import {
	type JsonSchema,
	fieldKind,
	objectFields,
	resolveRef,
} from "@/lib/schema-form";
import { useState } from "react";

const inputStyle = {
	background: "var(--bg)",
	borderColor: "var(--border)",
	color: "var(--fg)",
};
const inputClass = "w-full px-3 py-2 rounded border text-sm";

/** A labeled field row; the schema `description` is the tooltip (the whole point of driving the
 * form from the schema). */
function Labeled({
	name,
	schema,
	required,
	children,
}: {
	name: string;
	schema: JsonSchema;
	required: boolean;
	children: React.ReactNode;
}) {
	const desc =
		typeof schema.description === "string" ? schema.description : undefined;
	return (
		<div className="mb-3">
			<div className="mb-1 flex items-center gap-1">
				<span className="text-sm font-medium">{name}</span>
				{required ? <span style={{ color: "var(--error)" }}>*</span> : null}
				{desc ? (
					<span
						title={desc}
						className="cursor-help text-xs"
						style={{ color: "var(--fg-muted)" }}
					>
						ⓘ
					</span>
				) : null}
			</div>
			{children}
		</div>
	);
}

function defaultFor(schema: JsonSchema): unknown {
	switch (fieldKind(schema)) {
		case "boolean":
			return false;
		case "number":
			return 0;
		case "array":
			return [];
		case "object":
			return {};
		default:
			return "";
	}
}

function JsonField({
	value,
	onChange,
}: {
	value: unknown;
	onChange: (v: unknown) => void;
}) {
	const [text, setText] = useState(() =>
		JSON.stringify(value ?? null, null, 2),
	);
	const [error, setError] = useState<string | null>(null);
	return (
		<div>
			<textarea
				className={`${inputClass} font-mono`}
				style={{ ...inputStyle, minHeight: 80 }}
				value={text}
				spellCheck={false}
				onChange={(e) => {
					setText(e.target.value);
					try {
						onChange(JSON.parse(e.target.value));
						setError(null);
					} catch {
						setError("invalid JSON");
					}
				}}
			/>
			{error ? (
				<p className="text-xs" style={{ color: "var(--error)" }}>
					{error}
				</p>
			) : null}
		</div>
	);
}

function ArrayField({
	root,
	schema,
	value,
	onChange,
}: {
	root: JsonSchema;
	schema: JsonSchema;
	value: unknown[];
	onChange: (v: unknown[]) => void;
}) {
	const items = resolveRef(root, (schema.items ?? {}) as JsonSchema);
	return (
		<div className="space-y-2">
			{value.map((item, i) => (
				<div
					key={`${i}:${typeof item === "object" ? JSON.stringify(item).length : String(item)}`}
					className="flex items-start gap-2"
				>
					<div className="flex-1">
						<Field
							root={root}
							schema={items}
							name=""
							value={item}
							onChange={(v) => {
								const next = [...value];
								next[i] = v;
								onChange(next);
							}}
						/>
					</div>
					<button
						type="button"
						onClick={() => onChange(value.filter((_, j) => j !== i))}
						className="rounded border px-2 py-1 text-xs"
						style={{ borderColor: "var(--border)" }}
					>
						×
					</button>
				</div>
			))}
			<button
				type="button"
				onClick={() => onChange([...value, defaultFor(items)])}
				className="rounded border px-2 py-1 text-xs"
				style={{ borderColor: "var(--border)" }}
			>
				+ Add
			</button>
		</div>
	);
}

function ObjectFields({
	root,
	schema,
	value,
	onChange,
	nested,
}: {
	root: JsonSchema;
	schema: JsonSchema;
	value: Record<string, unknown>;
	onChange: (v: Record<string, unknown>) => void;
	nested?: boolean;
}) {
	const fields = objectFields(root, schema);
	return (
		<div
			className={nested ? "border-l pl-3" : ""}
			style={nested ? { borderColor: "var(--border)" } : undefined}
		>
			{fields.map((f) => (
				<Labeled
					key={f.name}
					name={f.name}
					schema={f.schema}
					required={f.required}
				>
					<Field
						root={root}
						schema={f.schema}
						name={f.name}
						value={value[f.name]}
						onChange={(v) => onChange({ ...value, [f.name]: v })}
					/>
				</Labeled>
			))}
		</div>
	);
}

function Field({
	root,
	schema,
	name,
	value,
	onChange,
}: {
	root: JsonSchema;
	schema: JsonSchema;
	name: string;
	value: unknown;
	onChange: (v: unknown) => void;
}) {
	switch (fieldKind(schema, name)) {
		case "const":
			return (
				<div className={`${inputClass} font-mono`} style={inputStyle}>
					{String(schema.const)}
				</div>
			);
		case "enum":
			return (
				<select
					className={inputClass}
					style={inputStyle}
					value={String(value ?? "")}
					onChange={(e) => onChange(e.target.value)}
				>
					<option value="" />
					{(schema.enum as unknown[]).map((o) => (
						<option key={String(o)} value={String(o)}>
							{String(o)}
						</option>
					))}
				</select>
			);
		case "boolean":
			return (
				<input
					type="checkbox"
					checked={Boolean(value)}
					onChange={(e) => onChange(e.target.checked)}
				/>
			);
		case "number":
			return (
				<input
					type="number"
					className={inputClass}
					style={inputStyle}
					value={value === undefined || value === null ? "" : Number(value)}
					onChange={(e) =>
						onChange(e.target.value === "" ? undefined : Number(e.target.value))
					}
				/>
			);
		case "text":
			return (
				<textarea
					className={`${inputClass} font-mono`}
					style={{ ...inputStyle, minHeight: 100 }}
					value={String(value ?? "")}
					spellCheck={false}
					onChange={(e) => onChange(e.target.value)}
				/>
			);
		case "string":
			return (
				<input
					type="text"
					className={inputClass}
					style={inputStyle}
					value={String(value ?? "")}
					onChange={(e) => onChange(e.target.value)}
				/>
			);
		case "object":
			return (
				<ObjectFields
					root={root}
					schema={schema}
					value={(value as Record<string, unknown>) ?? {}}
					onChange={onChange as (v: Record<string, unknown>) => void}
					nested
				/>
			);
		case "array":
			return (
				<ArrayField
					root={root}
					schema={schema}
					value={(value as unknown[]) ?? []}
					onChange={onChange}
				/>
			);
		default:
			return <JsonField value={value} onChange={onChange} />;
	}
}

/** Render an editable form for a whole artifact from its JSON Schema. Every field comes from the
 * schema (type + enum + constraints), each with its `description` as a tooltip. */
export function SchemaForm({
	schema,
	value,
	onChange,
}: {
	schema: JsonSchema;
	value: Record<string, unknown>;
	onChange: (v: Record<string, unknown>) => void;
}) {
	return (
		<ObjectFields
			root={schema}
			schema={schema}
			value={value}
			onChange={onChange}
		/>
	);
}

"use client";

import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";

/** Toggles the `.dark` class on <html> and persists the choice. */
export function ThemeToggle() {
	const [dark, setDark] = useState(true);

	useEffect(() => {
		const stored = localStorage.getItem("theme");
		const isDark = stored ? stored === "dark" : true;
		setDark(isDark);
		document.documentElement.classList.toggle("dark", isDark);
	}, []);

	function toggle() {
		const next = !dark;
		setDark(next);
		document.documentElement.classList.toggle("dark", next);
		localStorage.setItem("theme", next ? "dark" : "light");
	}

	return (
		<Button
			variant="ghost"
			size="icon"
			onClick={toggle}
			aria-label="Toggle theme"
		>
			{dark ? <Moon /> : <Sun />}
		</Button>
	);
}

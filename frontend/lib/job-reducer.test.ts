import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

describe("frontend test harness", () => {
  it("renders with React Testing Library and loads jest-dom matchers", () => {
    render(React.createElement("button", { disabled: true }, "Unavailable"));

    expect(screen.getByRole("button", { name: "Unavailable" })).toBeDisabled();
  });
});

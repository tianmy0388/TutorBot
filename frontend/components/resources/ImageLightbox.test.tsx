import { StrictMode, useState } from "react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import {
  ImageLightbox,
  type ImageArtifact,
} from "./ImageLightbox";

const images: ImageArtifact[] = [
  { name: "one.png", url: "/api/artifacts/one.png", kind: "png" },
  { name: "two.svg", url: "/api/artifacts/two.svg", kind: "svg" },
];

class TestPointerEvent extends MouseEvent {
  readonly pointerId: number;
  readonly pointerType: string;

  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 0;
    this.pointerType = init.pointerType ?? "mouse";
  }
}

function OpenableLightbox({ initialIndex = 0 }: { initialIndex?: number }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        打开图片
      </button>
      <ImageLightbox
        images={images}
        initialIndex={initialIndex}
        open={open}
        onOpenChange={setOpen}
      />
    </>
  );
}

beforeEach(() => {
  document.body.style.overflow = "auto";
  vi.stubGlobal("PointerEvent", TestPointerEvent);
  Object.defineProperty(HTMLElement.prototype, "setPointerCapture", {
    configurable: true,
    value: vi.fn(),
  });
  Object.defineProperty(HTMLElement.prototype, "releasePointerCapture", {
    configurable: true,
    value: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ImageLightbox accessibility and lifecycle", () => {
  it("focuses the dialog, traps focus, closes with Escape, and restores the exact opener", async () => {
    render(<OpenableLightbox />);
    const opener = screen.getByRole("button", { name: "打开图片" });
    opener.focus();
    fireEvent.click(opener);

    const dialog = screen.getByRole("dialog", { name: "图片查看器" });
    await waitFor(() => expect(dialog).toHaveFocus());
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(screen.getByRole("button", { name: "关闭图片查看器" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(screen.getByRole("button", { name: "上一张图片" })).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "图片查看器" })).not.toBeInTheDocument(),
    );
    expect(opener).toHaveFocus();
    expect(document.body.style.overflow).toBe("auto");
  });

  it("restores listeners and body scrolling when unmounted", () => {
    const onOpenChange = vi.fn();
    const view = render(
      <ImageLightbox
        images={images}
        initialIndex={0}
        open
        onOpenChange={onOpenChange}
      />,
    );
    expect(document.body.style.overflow).toBe("hidden");
    onOpenChange.mockClear();

    view.unmount();
    expect(document.body.style.overflow).toBe("auto");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it("keeps focus and scroll cleanup balanced under StrictMode", async () => {
    render(
      <StrictMode>
        <OpenableLightbox />
      </StrictMode>,
    );
    const opener = screen.getByRole("button", { name: "打开图片" });
    opener.focus();
    fireEvent.click(opener);
    await waitFor(() =>
      expect(screen.getByRole("dialog", { name: "图片查看器" })).toHaveFocus(),
    );
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(opener).toHaveFocus());
    expect(document.body.style.overflow).toBe("auto");
  });

  it("closes safely when there are no images", async () => {
    const onOpenChange = vi.fn();
    render(
      <ImageLightbox
        images={[]}
        initialIndex={0}
        open
        onOpenChange={onOpenChange}
      />,
    );

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(document.body.style.overflow).toBe("auto");
  });
});

describe("ImageLightbox navigation and transforms", () => {
  it("clamps zoom between 25% and 500% and reset returns to the origin", () => {
    render(
      <ImageLightbox images={images} initialIndex={0} open onOpenChange={vi.fn()} />,
    );
    const zoomIn = screen.getByRole("button", { name: "放大" });
    const zoomOut = screen.getByRole("button", { name: "缩小" });
    const image = screen.getByRole("img", { name: "one.png" });

    for (let i = 0; i < 30; i += 1) fireEvent.click(zoomIn);
    expect(screen.getByText("500%")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "+" });
    expect(screen.getByText("500%")).toBeInTheDocument();

    fireEvent.pointerDown(image, {
      pointerId: 1,
      pointerType: "mouse",
      button: 0,
      clientX: 20,
      clientY: 20,
    });
    fireEvent.pointerMove(image, {
      pointerId: 1,
      pointerType: "mouse",
      clientX: 45,
      clientY: 60,
    });
    expect(image.style.transform).toContain("translate(25px, 40px)");

    for (let i = 0; i < 30; i += 1) fireEvent.click(zoomOut);
    expect(screen.getByText("25%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重置图片" }));
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(image.style.transform).toBe("translate(0px, 0px) scale(1)");
  });

  it("supports every zoom and navigation keyboard shortcut", () => {
    render(
      <ImageLightbox images={images} initialIndex={0} open onOpenChange={vi.fn()} />,
    );

    fireEvent.keyDown(document, { key: "+" });
    expect(screen.getByText("125%")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "=" });
    expect(screen.getByText("150%")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "-" });
    expect(screen.getByText("125%")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "0" });
    expect(screen.getByText("100%")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "ArrowRight" });
    expect(screen.getByRole("img", { name: "two.svg" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "ArrowLeft" });
    expect(screen.getByRole("img", { name: "one.png" })).toBeInTheDocument();
  });

  it("clamps the initial index and resets transform when switching images", () => {
    render(
      <ImageLightbox images={images} initialIndex={99} open onOpenChange={vi.fn()} />,
    );
    expect(screen.getByRole("img", { name: "two.svg" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "放大" }));
    expect(screen.getByText("125%")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "上一张图片" }));
    const first = screen.getByRole("img", { name: "one.png" });
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(first.style.transform).toBe("translate(0px, 0px) scale(1)");
    fireEvent.click(screen.getByRole("button", { name: "下一张图片" }));
    expect(screen.getByRole("img", { name: "two.svg" })).toBeInTheDocument();
  });

  it("clamps and resets when the image list becomes shorter", () => {
    const onOpenChange = vi.fn();
    const view = render(
      <ImageLightbox
        images={images}
        initialIndex={1}
        open
        onOpenChange={onOpenChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "放大" }));
    expect(screen.getByText("125%")).toBeInTheDocument();

    view.rerender(
      <ImageLightbox
        images={[images[0]]}
        initialIndex={1}
        open
        onOpenChange={onOpenChange}
      />,
    );
    expect(screen.getByRole("img", { name: "one.png" })).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
  });

  it("does not reset selection or zoom for an equivalent new array", () => {
    const onOpenChange = vi.fn();
    const view = render(
      <ImageLightbox
        images={images}
        initialIndex={0}
        open
        onOpenChange={onOpenChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "下一张图片" }));
    fireEvent.click(screen.getByRole("button", { name: "放大" }));

    view.rerender(
      <ImageLightbox
        images={[...images]}
        initialIndex={0}
        open
        onOpenChange={onOpenChange}
      />,
    );
    expect(screen.getByRole("img", { name: "two.svg" })).toBeInTheDocument();
    expect(screen.getByText("125%")).toBeInTheDocument();
  });

  it("ends a drag on pointer cancel without stale movement", () => {
    render(
      <ImageLightbox images={images} initialIndex={0} open onOpenChange={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "放大" }));
    const image = screen.getByRole("img", { name: "one.png" });

    fireEvent.pointerDown(image, {
      pointerId: 7,
      pointerType: "mouse",
      button: 0,
      clientX: 10,
      clientY: 15,
    });
    fireEvent.pointerMove(image, {
      pointerId: 7,
      pointerType: "mouse",
      clientX: 30,
      clientY: 45,
    });
    const movedTransform = image.style.transform;
    fireEvent.pointerCancel(image, { pointerId: 7, pointerType: "mouse" });
    fireEvent.pointerMove(image, {
      pointerId: 7,
      pointerType: "mouse",
      clientX: 90,
      clientY: 100,
    });

    expect(image.style.transform).toBe(movedTransform);
    expect(HTMLElement.prototype.releasePointerCapture).toHaveBeenCalledWith(7);
  });

  it("ends a drag on ordinary pointer up and releases capture", () => {
    render(
      <ImageLightbox images={images} initialIndex={0} open onOpenChange={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "放大" }));
    const image = screen.getByRole("img", { name: "one.png" });

    fireEvent.pointerDown(image, {
      pointerId: 8,
      pointerType: "mouse",
      button: 0,
      clientX: 10,
      clientY: 10,
    });
    fireEvent.pointerMove(image, {
      pointerId: 8,
      pointerType: "mouse",
      clientX: 30,
      clientY: 40,
    });
    fireEvent.pointerUp(image, {
      pointerId: 8,
      pointerType: "mouse",
      clientX: 30,
      clientY: 40,
    });
    const settled = image.style.transform;
    fireEvent.pointerMove(image, {
      pointerId: 8,
      pointerType: "mouse",
      clientX: 80,
      clientY: 90,
    });

    expect(image.style.transform).toBe(settled);
    expect(HTMLElement.prototype.releasePointerCapture).toHaveBeenCalledWith(8);
  });

  it("zooms around the wheel cursor and prevents page scrolling", () => {
    render(
      <ImageLightbox images={images} initialIndex={0} open onOpenChange={vi.fn()} />,
    );
    const stage = screen.getByTestId("image-lightbox-stage");
    vi.spyOn(stage, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 400,
      bottom: 300,
      width: 400,
      height: 300,
      toJSON: () => ({}),
    });
    const wheel = new WheelEvent("wheel", {
      bubbles: true,
      cancelable: true,
      deltaY: -100,
      clientX: 300,
      clientY: 200,
    });

    expect(fireEvent(stage, wheel)).toBe(false);
    expect(wheel.defaultPrevented).toBe(true);
    expect(screen.getByText("125%")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "one.png" }).style.transform).toBe(
      "translate(-25px, -12.5px) scale(1.25)",
    );
  });

  it("removes and reinstalls exactly one native wheel listener across close and reopen", async () => {
    const addListener = vi.spyOn(EventTarget.prototype, "addEventListener");
    const removeListener = vi.spyOn(EventTarget.prototype, "removeEventListener");
    render(<OpenableLightbox />);
    const opener = screen.getByRole("button", { name: "打开图片" });
    opener.focus();
    fireEvent.click(opener);

    const wheelAdds = () =>
      addListener.mock.calls.filter(
        ([type, , options]) =>
          type === "wheel" &&
          typeof options === "object" &&
          options !== null &&
          "passive" in options &&
          options.passive === false,
      );
    expect(wheelAdds()).toHaveLength(1);
    const firstHandler = wheelAdds()[0][1];

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(opener).toHaveFocus());
    expect(
      removeListener.mock.calls.some(
        ([type, handler]) => type === "wheel" && handler === firstHandler,
      ),
    ).toBe(true);

    fireEvent.click(opener);
    expect(wheelAdds()).toHaveLength(2);
    expect(wheelAdds()[1][1]).not.toBe(firstHandler);
    const reopenedStage = screen.getByTestId("image-lightbox-stage");
    fireEvent(
      reopenedStage,
      new WheelEvent("wheel", {
        bubbles: true,
        cancelable: true,
        deltaY: -100,
      }),
    );
    expect(screen.getByText("125%")).toBeInTheDocument();
  });

  it("supports a two-pointer pinch and cleans both pointers", () => {
    render(
      <ImageLightbox images={images} initialIndex={0} open onOpenChange={vi.fn()} />,
    );
    const image = screen.getByRole("img", { name: "one.png" });
    const stage = screen.getByTestId("image-lightbox-stage");
    vi.spyOn(stage, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 400,
      bottom: 300,
      width: 400,
      height: 300,
      toJSON: () => ({}),
    });

    fireEvent.pointerDown(image, {
      pointerId: 1,
      pointerType: "touch",
      clientX: 250,
      clientY: 150,
    });
    fireEvent.pointerDown(image, {
      pointerId: 2,
      pointerType: "touch",
      clientX: 350,
      clientY: 150,
    });
    fireEvent.pointerMove(image, {
      pointerId: 2,
      pointerType: "touch",
      clientX: 450,
      clientY: 150,
    });
    expect(screen.getByText("200%")).toBeInTheDocument();
    expect(image.style.transform).toBe("translate(-50px, 0px) scale(2)");

    fireEvent.pointerUp(image, { pointerId: 1, pointerType: "touch" });
    const settled = image.style.transform;
    fireEvent.pointerMove(image, {
      pointerId: 2,
      pointerType: "touch",
      clientX: 500,
      clientY: 150,
    });
    expect(image.style.transform).toBe(settled);
    fireEvent.pointerCancel(image, { pointerId: 2, pointerType: "touch" });
    expect(HTMLElement.prototype.releasePointerCapture).toHaveBeenCalledWith(1);
    expect(HTMLElement.prototype.releasePointerCapture).toHaveBeenCalledWith(2);
  });
});

describe("ImageLightbox artifacts and failure states", () => {
  it("downloads the selected canonical URL with its suggested filename", () => {
    render(
      <ImageLightbox images={images} initialIndex={1} open onOpenChange={vi.fn()} />,
    );
    const download = screen.getByRole("link", { name: "下载 two.svg" });
    expect(download).toHaveAttribute("href", "/api/artifacts/two.svg");
    expect(download).toHaveAttribute("download", "two.svg");
  });

  it("shows a per-image load failure while healthy siblings remain navigable", () => {
    render(
      <ImageLightbox images={images} initialIndex={0} open onOpenChange={vi.fn()} />,
    );
    fireEvent.error(screen.getByRole("img", { name: "one.png" }));
    expect(screen.getByText("one.png 加载失败")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "下一张图片" }));
    expect(screen.getByRole("img", { name: "two.svg" })).toBeInTheDocument();
    expect(screen.queryByText("one.png 加载失败")).not.toBeInTheDocument();
  });

  it("exposes responsive control and intrinsic image layout hooks", () => {
    render(
      <ImageLightbox images={images} initialIndex={0} open onOpenChange={vi.fn()} />,
    );
    expect(screen.getByTestId("image-lightbox-controls")).toHaveClass(
      "image-lightbox-controls",
      "flex-wrap",
    );
    expect(screen.getByRole("img", { name: "one.png" })).toHaveClass(
      "image-lightbox-image",
      "object-contain",
    );
  });
});

//go:build windows

package main

import (
	"fmt"
	"runtime"
	"strings"
	"sync"
	"syscall"
	"unsafe"
)

const (
	wmDestroy        = 0x0002
	wmClose          = 0x0010
	wmCommand        = 0x0111
	wmCtlColorEdit   = 0x0133
	wmCtlColorStatic = 0x0138
	wmSetIcon        = 0x0080
	wmSetFont        = 0x0030
	wmAppUpdate      = 0x8001
	mbIconError      = 0x00000010
	mbOK             = 0x00000000
	swShow           = 5
	cwUseDefault     = 0x80000000
	wsOverlapped     = 0x00000000
	wsCaption        = 0x00C00000
	wsSysMenu        = 0x00080000
	wsMinimizeBox    = 0x00020000
	wsVisible        = 0x10000000
	wsChild          = 0x40000000
	wsBorder         = 0x00800000
	wsTabStop        = 0x00010000
	wsVScroll        = 0x00200000
	ssLeft           = 0x00000000
	esLeft           = 0x0000
	esMultiline      = 0x0004
	esAutoVScroll    = 0x0040
	esReadOnly       = 0x0800
	bsPushButton     = 0x00000000
	transparentMode  = 1
	colorWindow      = 5
	ecLeftMargin     = 0x0001
	ecRightMargin    = 0x0002
	emSetMargins     = 0x00D3
	iconSmall        = 0
	iconBig          = 1
	idiApplication   = 32512
	cmdOpenLog       = 1001
	cmdOpenWorkspace = 1002
	cmdExit          = 1003
)

var (
	user32               = syscall.NewLazyDLL("user32.dll")
	kernel32             = syscall.NewLazyDLL("kernel32.dll")
	gdi32                = syscall.NewLazyDLL("gdi32.dll")
	shell32              = syscall.NewLazyDLL("shell32.dll")
	procDefWindowProcW   = user32.NewProc("DefWindowProcW")
	procDispatchMessageW = user32.NewProc("DispatchMessageW")
	procGetMessageW      = user32.NewProc("GetMessageW")
	procCreateWindowExW  = user32.NewProc("CreateWindowExW")
	procDestroyWindow    = user32.NewProc("DestroyWindow")
	procGetSysColorBrush = user32.NewProc("GetSysColorBrush")
	procLoadIconW        = user32.NewProc("LoadIconW")
	procPostQuitMessage  = user32.NewProc("PostQuitMessage")
	procRegisterClassW   = user32.NewProc("RegisterClassW")
	procShowWindow       = user32.NewProc("ShowWindow")
	procUpdateWindow     = user32.NewProc("UpdateWindow")
	procSetWindowTextW   = user32.NewProc("SetWindowTextW")
	procPostMessageW     = user32.NewProc("PostMessageW")
	procSendMessageW     = user32.NewProc("SendMessageW")
	procMessageBoxW      = user32.NewProc("MessageBoxW")
	procGetModuleHandleW = kernel32.NewProc("GetModuleHandleW")
	procCreateFontW      = gdi32.NewProc("CreateFontW")
	procSetBkMode        = gdi32.NewProc("SetBkMode")
	procSetTextColor     = gdi32.NewProc("SetTextColor")
	procShellExecuteW    = shell32.NewProc("ShellExecuteW")

	currentStatusUI *winStatusUI
	windowProcPtr   = syscall.NewCallback(windowProc)
)

type winStatusUI struct {
	mu sync.Mutex

	hwnd              uintptr
	titleHwnd         uintptr
	subtitleHwnd      uintptr
	statusHwnd        uintptr
	detailHwnd        uintptr
	openLogHwnd       uintptr
	openWorkspaceHwnd uintptr
	exitHwnd          uintptr
	titleFont         uintptr
	subtitleFont      uintptr
	statusFont        uintptr
	bodyFont          uintptr
	buttonFont        uintptr

	workspacePath string
	logPath       string
	pending       string

	done       chan struct{}
	ready      chan error
	closedOnce sync.Once
}

type wndClass struct {
	style         uint32
	lpfnWndProc   uintptr
	cbClsExtra    int32
	cbWndExtra    int32
	hInstance     uintptr
	hIcon         uintptr
	hCursor       uintptr
	hbrBackground uintptr
	lpszMenuName  *uint16
	lpszClassName *uint16
}

type point struct {
	x int32
	y int32
}

type msg struct {
	hwnd    uintptr
	message uint32
	wParam  uintptr
	lParam  uintptr
	time    uint32
	pt      point
}

func startStatusUI(title, workspacePath, logPath, initialText string) (statusUI, error) {
	ui := &winStatusUI{
		done:          make(chan struct{}),
		ready:         make(chan error, 1),
		workspacePath: workspacePath,
		logPath:       logPath,
		pending:       initialText,
	}
	go ui.run(title)
	if err := <-ui.ready; err != nil {
		return nil, err
	}
	return ui, nil
}

func (u *winStatusUI) run(title string) {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()

	currentStatusUI = u

	className, _ := syscall.UTF16PtrFromString("TelecodexStatusWindow")
	titlePtr, _ := syscall.UTF16PtrFromString(title)
	instance, _, _ := procGetModuleHandleW.Call(0)
	appIcon, _, _ := procLoadIconW.Call(0, idiApplication)

	wc := wndClass{
		lpfnWndProc:   windowProcPtr,
		hInstance:     instance,
		hIcon:         appIcon,
		hbrBackground: 6,
		lpszClassName: className,
	}
	if atom, _, err := procRegisterClassW.Call(uintptr(unsafe.Pointer(&wc))); atom == 0 {
		u.ready <- fmt.Errorf("register window class: %v", err)
		return
	}

	hwnd, _, err := procCreateWindowExW.Call(
		0,
		uintptr(unsafe.Pointer(className)),
		uintptr(unsafe.Pointer(titlePtr)),
		uintptr(wsOverlapped|wsCaption|wsSysMenu|wsMinimizeBox|wsVisible),
		cwUseDefault,
		cwUseDefault,
		820,
		660,
		0,
		0,
		instance,
		0,
	)
	if hwnd == 0 {
		u.ready <- fmt.Errorf("create window: %v", err)
		return
	}
	u.hwnd = hwnd
	if appIcon != 0 {
		procSendMessageW.Call(hwnd, wmSetIcon, iconSmall, appIcon)
		procSendMessageW.Call(hwnd, wmSetIcon, iconBig, appIcon)
	}

	if err := u.createControls(instance); err != nil {
		u.ready <- err
		return
	}

	u.applyFonts()
	u.applyPendingText()

	procShowWindow.Call(hwnd, swShow)
	procUpdateWindow.Call(hwnd)
	u.ready <- nil

	var message msg
	for {
		ret, _, _ := procGetMessageW.Call(uintptr(unsafe.Pointer(&message)), 0, 0, 0)
		if int32(ret) <= 0 {
			break
		}
		procDispatchMessageW.Call(uintptr(unsafe.Pointer(&message)))
	}

	u.closedOnce.Do(func() { close(u.done) })
}

func (u *winStatusUI) createControls(instance uintptr) error {
	staticClass, _ := syscall.UTF16PtrFromString("STATIC")
	editClass, _ := syscall.UTF16PtrFromString("EDIT")
	buttonClass, _ := syscall.UTF16PtrFromString("BUTTON")

	makeStatic := func(text string, x, y, w, h int) (uintptr, error) {
		ptr, _ := syscall.UTF16PtrFromString(text)
		hwnd, _, err := procCreateWindowExW.Call(
			0,
			uintptr(unsafe.Pointer(staticClass)),
			uintptr(unsafe.Pointer(ptr)),
			uintptr(wsChild|wsVisible|ssLeft),
			uintptr(x),
			uintptr(y),
			uintptr(w),
			uintptr(h),
			u.hwnd,
			0,
			instance,
			0,
		)
		if hwnd == 0 {
			return 0, fmt.Errorf("create static control: %v", err)
		}
		return hwnd, nil
	}

	makeButton := func(text string, id, x, y, w, h int) (uintptr, error) {
		ptr, _ := syscall.UTF16PtrFromString(text)
		hwnd, _, err := procCreateWindowExW.Call(
			0,
			uintptr(unsafe.Pointer(buttonClass)),
			uintptr(unsafe.Pointer(ptr)),
			uintptr(wsChild|wsVisible|wsTabStop|bsPushButton),
			uintptr(x),
			uintptr(y),
			uintptr(w),
			uintptr(h),
			u.hwnd,
			uintptr(id),
			instance,
			0,
		)
		if hwnd == 0 {
			return 0, fmt.Errorf("create button control: %v", err)
		}
		return hwnd, nil
	}

	var err error
	u.titleHwnd, err = makeStatic("Telecodex", 32, 32, 320, 42)
	if err != nil {
		return err
	}
	u.subtitleHwnd, err = makeStatic("Local Telegram remote for Codex", 32, 82, 380, 24)
	if err != nil {
		return err
	}
	u.statusHwnd, err = makeStatic("Starting up", 32, 132, 640, 28)
	if err != nil {
		return err
	}

	initialDetail, _ := syscall.UTF16PtrFromString("Preparing local Telegram bridge...")
	detailHwnd, _, err := procCreateWindowExW.Call(
		0,
		uintptr(unsafe.Pointer(editClass)),
		uintptr(unsafe.Pointer(initialDetail)),
		uintptr(wsChild|wsVisible|wsBorder|wsVScroll|esLeft|esMultiline|esAutoVScroll|esReadOnly),
		32,
		174,
		730,
		334,
		u.hwnd,
		0,
		instance,
		0,
	)
	if detailHwnd == 0 {
		return fmt.Errorf("create detail control: %v", err)
	}
	u.detailHwnd = detailHwnd
	procSendMessageW.Call(u.detailHwnd, emSetMargins, ecLeftMargin|ecRightMargin, makeLParam(16, 16))

	u.openLogHwnd, err = makeButton("Open Log", cmdOpenLog, 32, 534, 136, 42)
	if err != nil {
		return err
	}
	u.openWorkspaceHwnd, err = makeButton("Open Workspace", cmdOpenWorkspace, 184, 534, 182, 42)
	if err != nil {
		return err
	}
	u.exitHwnd, err = makeButton("Exit", cmdExit, 658, 534, 104, 42)
	if err != nil {
		return err
	}
	return nil
}

func (u *winStatusUI) applyFonts() {
	u.titleFont = createFont("Segoe UI", 26, 700)
	u.subtitleFont = createFont("Segoe UI", 12, 400)
	u.statusFont = createFont("Segoe UI", 15, 600)
	u.bodyFont = createFont("Segoe UI", 13, 400)
	u.buttonFont = createFont("Segoe UI", 12, 500)

	setControlFont(u.titleHwnd, u.titleFont)
	setControlFont(u.subtitleHwnd, u.subtitleFont)
	setControlFont(u.statusHwnd, u.statusFont)
	setControlFont(u.detailHwnd, u.bodyFont)
	setControlFont(u.openLogHwnd, u.buttonFont)
	setControlFont(u.openWorkspaceHwnd, u.buttonFont)
	setControlFont(u.exitHwnd, u.buttonFont)
}

func (u *winStatusUI) applyPendingText() {
	status, detail := splitStatusText(u.pending)
	setWindowText(u.statusHwnd, status)
	setWindowText(u.detailHwnd, normalizeMultiline(detail))
}

func (u *winStatusUI) SetStatus(text string) {
	u.mu.Lock()
	u.pending = text
	hwnd := u.hwnd
	u.mu.Unlock()
	if hwnd != 0 {
		procPostMessageW.Call(hwnd, wmAppUpdate, 0, 0)
	}
}

func (u *winStatusUI) Close() {
	u.mu.Lock()
	hwnd := u.hwnd
	u.mu.Unlock()
	if hwnd != 0 {
		procPostMessageW.Call(hwnd, wmClose, 0, 0)
	}
}

func (u *winStatusUI) Done() <-chan struct{} { return u.done }

func (u *winStatusUI) handleCommand(id int) {
	switch id {
	case cmdOpenLog:
		openPath(u.logPath)
	case cmdOpenWorkspace:
		openPath(u.workspacePath)
	case cmdExit:
		u.Close()
	}
}

func windowProc(hwnd uintptr, msg uint32, wParam, lParam uintptr) uintptr {
	switch msg {
	case wmAppUpdate:
		if currentStatusUI != nil {
			currentStatusUI.mu.Lock()
			currentStatusUI.applyPendingText()
			currentStatusUI.mu.Unlock()
		}
		return 0
	case wmCommand:
		if currentStatusUI != nil {
			currentStatusUI.handleCommand(int(wParam & 0xffff))
		}
		return 0
	case wmCtlColorStatic:
		if currentStatusUI != nil {
			return currentStatusUI.handleStaticColor(wParam, lParam)
		}
		return 0
	case wmCtlColorEdit:
		if currentStatusUI != nil {
			return currentStatusUI.handleEditColor(wParam, lParam)
		}
		return 0
	case wmClose:
		procDestroyWindow.Call(hwnd)
		return 0
	case wmDestroy:
		procPostQuitMessage.Call(0)
		if currentStatusUI != nil {
			currentStatusUI.closedOnce.Do(func() { close(currentStatusUI.done) })
		}
		return 0
	default:
		ret, _, _ := procDefWindowProcW.Call(hwnd, uintptr(msg), wParam, lParam)
		return ret
	}
}

func createFont(face string, pointSize int, weight int) uintptr {
	facePtr, _ := syscall.UTF16PtrFromString(face)
	handle, _, _ := procCreateFontW.Call(
		uintptr(-pointSize),
		0, 0, 0,
		uintptr(weight),
		0, 0, 0,
		1,
		0, 0, 0, 0,
		uintptr(unsafe.Pointer(facePtr)),
	)
	return handle
}

func setControlFont(hwnd, font uintptr) {
	if hwnd == 0 || font == 0 {
		return
	}
	procSendMessageW.Call(hwnd, wmSetFont, font, 1)
}

func setWindowText(hwnd uintptr, text string) {
	if hwnd == 0 {
		return
	}
	ptr, _ := syscall.UTF16PtrFromString(text)
	procSetWindowTextW.Call(hwnd, uintptr(unsafe.Pointer(ptr)))
}

func (u *winStatusUI) handleStaticColor(hdc, hwnd uintptr) uintptr {
	if hwnd == u.titleHwnd {
		setDCTextStyle(hdc, rgb(17, 24, 39))
	} else if hwnd == u.subtitleHwnd {
		setDCTextStyle(hdc, rgb(107, 114, 128))
	} else if hwnd == u.statusHwnd {
		setDCTextStyle(hdc, rgb(37, 99, 235))
	} else {
		setDCTextStyle(hdc, rgb(31, 41, 55))
	}

	brush, _, _ := procGetSysColorBrush.Call(colorWindow)
	return brush
}

func (u *winStatusUI) handleEditColor(hdc, hwnd uintptr) uintptr {
	_ = hdc
	_ = hwnd
	brush, _, _ := procGetSysColorBrush.Call(colorWindow)
	return brush
}

func setDCTextStyle(hdc uintptr, color uintptr) {
	procSetBkMode.Call(hdc, transparentMode)
	procSetTextColor.Call(hdc, color)
}

func splitStatusText(text string) (string, string) {
	text = strings.ReplaceAll(text, "\r\n", "\n")
	text = strings.TrimSpace(text)
	if text == "" {
		return "Ready", "Waiting for Telegram messages."
	}

	lines := strings.Split(text, "\n")
	headline := "Idle"
	bodyStart := 0
	for i, line := range lines {
		line = strings.TrimSpace(line)
		if line != "" {
			headline = line
			bodyStart = i + 1
			break
		}
	}

	body := strings.TrimSpace(strings.Join(lines[bodyStart:], "\n"))
	if body == "" {
		body = "No additional details yet."
	}
	return headline, body
}

func rgb(r, g, b byte) uintptr {
	return uintptr(uint32(r) | uint32(g)<<8 | uint32(b)<<16)
}

func normalizeMultiline(text string) string {
	text = strings.ReplaceAll(text, "\r\n", "\n")
	text = strings.ReplaceAll(text, "\r", "\n")
	text = strings.ReplaceAll(text, "\n", "\r\n")
	return text
}

func makeLParam(low, high uint16) uintptr {
	return uintptr(uint32(low) | (uint32(high) << 16))
}

func openPath(path string) {
	if strings.TrimSpace(path) == "" {
		return
	}
	openVerb, _ := syscall.UTF16PtrFromString("open")
	target, _ := syscall.UTF16PtrFromString(path)
	procShellExecuteW.Call(0, uintptr(unsafe.Pointer(openVerb)), uintptr(unsafe.Pointer(target)), 0, 0, swShow)
}

func showFatalError(title, message string) {
	titlePtr, _ := syscall.UTF16PtrFromString(title)
	messagePtr, _ := syscall.UTF16PtrFromString(message)
	procMessageBoxW.Call(0, uintptr(unsafe.Pointer(messagePtr)), uintptr(unsafe.Pointer(titlePtr)), mbOK|mbIconError)
}

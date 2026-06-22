import app
import time
import math
import random
import settings

from events.input import Buttons, BUTTON_TYPES

# Added so running in emulator without the IMU doesn't crash the app
try:
    import imu
except Exception:
    imu = None

# Import LED controller
try:
    from tildagonos import tildagonos
except Exception:
    tildagonos = None

# Disable default LED pattern. Re-enabled on exit so the badge returns to pattern set before opening
try:
    from system.eventbus import eventbus
except Exception:
    eventbus = None
try:
    from system.patterndisplay.events import PatternDisable
except Exception:
    PatternDisable = None
try:
    from system.patterndisplay.events import PatternEnable
except Exception:
    PatternEnable = None

SCREEN_R = 120          
NUM_STARS = 55
NUM_LEDS = 12
LED_BRIGHTNESS = 0.6    # Change LED brightness

MODES = ("twinkle", "drift", "warp")   # cycle order

NAME_COLOURS = [
    (1.0, 1.0, 1.0),   # white
    (1.0, 0.4, 0.4),   # red
    (0.5, 1.0, 0.5),   # green
    (0.5, 0.7, 1.0),   # blue
    (1.0, 0.9, 0.3),   # yellow
    (1.0, 0.5, 1.0),   # magenta
    (0.4, 1.0, 1.0),   # cyan
    (1.0, 0.7, 0.3),   # orange
]

# Change ratio to change sensitivity of shake detection. Lower = More sensitive
SHAKE_RATIO = 0.35
SHAKE_COOLDOWN_MS = 450

# Name display
MAX_NAME_WIDTH = 195    # names wider than this scroll as a marquee
MARQUEE_SPEED = 45.0    # px/sec
MARQUEE_GAP = 50        # gap between repeats of a scrolling name

# Warp motion 
WARP_ACCEL = 1.9
WARP_BASE_SPEED = 11.0

# Hint Settings
HINT_FONT = 13
HINT_Y = 72

# Directions
DIR_UP = -math.pi / 2          # A 
DIR_DOWN = math.pi / 2         # D 
DIR_C = math.radians(30)       # C 
DIR_E = math.radians(150)      # E 


def _rand_point_in_circle():
    while True:
        x = random.uniform(-SCREEN_R, SCREEN_R)
        y = random.uniform(-SCREEN_R, SCREEN_R)
        if x * x + y * y <= SCREEN_R * SCREEN_R:
            return x, y


class StarNameApp(app.App):
    def __init__(self):
        self.button_states = Buttons(self)

        name = settings.get("name", "") or ""
        self.name = name.strip() or "Tildagon"
        self.font_size = self._fit_font(self.name)
        self._name_w = None          # measured lazily on first draw

        self.mode_index = 0
        self.colour_index = 0
        self.hints_visible = True
        self._first_frame = True

        self.effects = {
            "twinkle": (None, self._draw_twinkle),
            "drift": (self._advance_drift, self._draw_drift),
            "warp": (self._advance_warp, self._draw_warp),
        }

        # Buttons and actions
        self.button_actions = (
            ("UP", lambda: self._cycle_mode(-1)),       # A -> previous effect
            ("DOWN", lambda: self._cycle_mode(1)),      # D -> next effect
            ("CONFIRM", lambda: self._cycle_colour(1)),  # C -> next colour
            ("LEFT", lambda: self._cycle_colour(-1)),    # E -> previous colour
        )

        self.twinkle_stars = self._make_twinkle()
        self.drift_stars = self._make_drift()
        self.warp_stars = self._make_warp()

        self._last_ms = time.ticks_ms()
        self._t = 0.0

        self._rest_g = None
        self._cooldown_until = 0
        self._exiting = False

        # LEDs did a bright flash on starting. This stops it. Comment out if wanted
        self._disable_pattern()

    # ---------- setup ----------
    # Works out font size based on number of name characters set in badge settins     
    def _fit_font(self, text):
        n = len(text)
        if n <= 6:
            return 40
        if n <= 9:
            return 32
        if n <= 12:
            return 26
        return 22       # longer names scroll, so keep a readable size
    # Make an initial star as a starting point for the scenes     
    def _base_star(self):
        x, y = _rand_point_in_circle()
        return {"x": x, "y": y, "size": random.choice((1, 1, 1, 2, 2, 3))}
    # Builds the star list for the twinkle effect     
    def _make_twinkle(self):
        out = []
        for _ in range(NUM_STARS):
            s = self._base_star()
            s["phase"] = random.uniform(0, 6.2832)
            s["speed"] = random.uniform(1.5, 4.0)
            out.append(s)
        return out
    # Builds the stars for the drift effect 
    def _make_drift(self):
        out = []
        for _ in range(NUM_STARS):
            s = self._base_star()
            s["vx"] = random.uniform(-14, -4)    # px/sec
            s["vy"] = random.uniform(-5, 5)
            s["b"] = random.uniform(0.4, 1.0)
            out.append(s)
        return out
    # Builds the stars for the warp effect 
    def _make_warp(self):
        return [{"a": random.uniform(0, 6.2832),
                 "r": random.uniform(2, SCREEN_R),
                 "pr": 0.0} for _ in range(NUM_STARS)]

    # ---------- LEDs ----------
    
    # Disable the current badge pattern     
    def _disable_pattern(self):
        self._exiting = False           # we're taking the LEDs over again
        if eventbus is not None and PatternDisable is not None:
            try:
                eventbus.emit(PatternDisable())
            except Exception:
                pass
    # Set the LEDs to the same colour as the name     
    def _apply_leds(self):
        if tildagonos is None or self._exiting:
            return
        r, g, b = NAME_COLOURS[self.colour_index]
        m = 255 * LED_BRIGHTNESS
        col = (int(r * m), int(g * m), int(b * m))
        try:
            for i in range(1, NUM_LEDS + 1):
                tildagonos.leds[i] = col
            if hasattr(tildagonos.leds, "write"):
                tildagonos.leds.write()
        except Exception:
            pass
    # 	Go back to the badge LED pattern
    def _restore_leds(self):
        if eventbus is not None and PatternEnable is not None:
            try:
                eventbus.emit(PatternEnable())
            except Exception:
                pass
    # Moves to the next effect     
    def _cycle_mode(self, step):
        self.mode_index = (self.mode_index + step) % len(MODES)
    # Moves to the next colour 
    def _cycle_colour(self, step):
        self.colour_index = (self.colour_index + step) % len(NAME_COLOURS)

    # ---------- shake ----------
    def _acc_magnitude(self):
        if imu is None:
            return None
        try:
            data = imu.acc_read()
            ax, ay, az = data[0], data[1], data[2]
        except Exception:
            return None
        return math.sqrt(ax * ax + ay * ay + az * az)

    def _check_shake(self, now):
        mag = self._acc_magnitude()
        if mag is None:
            return
        if self._rest_g is None:
            self._rest_g = mag
            return
        if abs(mag - self._rest_g) <= SHAKE_RATIO * self._rest_g:
            self._rest_g = self._rest_g * 0.95 + mag * 0.05
        elif time.ticks_diff(now, self._cooldown_until) >= 0:
            self._cycle_colour(1)
            self.hints_visible = False
            self._cooldown_until = time.ticks_add(now, SHAKE_COOLDOWN_MS)

    # ---------- motion ----------
    def _advance_drift(self, dt):
        for s in self.drift_stars:
            s["x"] += s["vx"] * dt
            s["y"] += s["vy"] * dt
            if s["x"] < -SCREEN_R:
                s["x"] = SCREEN_R
            elif s["x"] > SCREEN_R:
                s["x"] = -SCREEN_R
            if s["y"] < -SCREEN_R:
                s["y"] = SCREEN_R
            elif s["y"] > SCREEN_R:
                s["y"] = -SCREEN_R

    def _advance_warp(self, dt):
        for s in self.warp_stars:
            s["pr"] = s["r"]
            s["r"] = s["r"] * (1 + WARP_ACCEL * dt) + WARP_BASE_SPEED * dt
            if s["r"] > SCREEN_R + 6:
                s["a"] = random.uniform(0, 6.2832)
                s["r"] = random.uniform(1, 5)
                s["pr"] = s["r"]

    # ---------- main loop ----------
    def update(self, delta):
        if self._first_frame:
            self._first_frame = False
            self._disable_pattern()     # re-assert in case __init__ was early

        b = self.button_states
        if b.get(BUTTON_TYPES["CANCEL"]):           # F -> exit
            b.clear()
            self._exiting = True        # stop our LED writes before handing back
            self._restore_leds()
            self.minimise()
            return
        for name, action in self.button_actions:
            if b.get(BUTTON_TYPES[name]):
                b.clear()
                self.hints_visible = False
                action()
                break

        now = time.ticks_ms()
        dt = time.ticks_diff(now, self._last_ms) / 1000.0
        self._last_ms = now
        if dt > 0.5:
            self._disable_pattern()
        if dt < 0:
            dt = 0
        elif dt > 0.1:
            dt = 0.1            
        self._t += dt

        advance = self.effects[MODES[self.mode_index]][0]
        if advance:
            advance(dt)

        self._check_shake(now)

    # ---------- drawing ----------
    def draw(self, ctx):
        ctx.save()
        ctx.rgb(0, 0, 0).rectangle(-120, -120, 240, 240).fill()

        self.effects[MODES[self.mode_index]][1](ctx)
        self._draw_name(ctx)
        if self.hints_visible:
            self._draw_hints(ctx)

        ctx.restore()

        # Re-assert the LEDs every frame
        self._apply_leds()

    def _draw_twinkle(self, ctx):
        t = self._t
        for s in self.twinkle_stars:
            b = 0.55 + 0.45 * math.sin(t * s["speed"] + s["phase"])
            if b < 0.15:
                b = 0.15
            ctx.rgb(b, b, b).rectangle(s["x"], s["y"], s["size"], s["size"]).fill()

    def _draw_drift(self, ctx):
        for s in self.drift_stars:
            b = s["b"]
            ctx.rgb(b, b, b).rectangle(s["x"], s["y"], s["size"], s["size"]).fill()

    def _draw_warp(self, ctx):
        for s in self.warp_stars:
            r = s["r"]
            pr = s["pr"]
            ca = math.cos(s["a"])
            sa = math.sin(s["a"])
            b = 0.2 + r / SCREEN_R
            if b > 1.0:
                b = 1.0
            ctx.line_width = max(0.6, (r / SCREEN_R) * 2.4)
            ctx.rgb(b, b, b).move_to(ca * pr, sa * pr).line_to(ca * r, sa * r).stroke()

    # ---------- name ----------
    def _name_width(self, ctx):
        if self._name_w is None:
            ctx.font_size = self.font_size
            try:
                self._name_w = ctx.text_width(self.name)
            except Exception:
                self._name_w = len(self.name) * self.font_size * 0.6
        return self._name_w

    def _stamp(self, ctx, text, x, y):
        o = max(1, self.font_size // 12)
        ctx.rgb(0, 0, 0)
        for dx, dy in ((-o, 0), (o, 0), (0, -o), (0, o)):
            ctx.move_to(x + dx, y + dy).text(text)
        r, g, b = NAME_COLOURS[self.colour_index]
        ctx.rgb(r, g, b).move_to(x, y).text(text)

    def _draw_name(self, ctx):
        ctx.text_baseline = ctx.MIDDLE
        ctx.font_size = self.font_size
        tw = self._name_width(ctx)

        if tw <= MAX_NAME_WIDTH:
            ctx.text_align = ctx.CENTER
            self._stamp(ctx, self.name, 0, 0)
            return

        ctx.text_align = ctx.LEFT
        period = tw + MARQUEE_GAP
        off = (self._t * MARQUEE_SPEED) % period
        x = SCREEN_R - off + period
        while x > -SCREEN_R - tw:
            self._stamp(ctx, self.name, x, 0)
            x -= period

    # ---------- hints ----------
    def _arrow(self, ctx, cx, cy, angle, size=6, g=0.6):
        ax = cx + size * math.cos(angle)
        ay = cy + size * math.sin(angle)
        b1x = cx + size * math.cos(angle + 2.5)
        b1y = cy + size * math.sin(angle + 2.5)
        b2x = cx + size * math.cos(angle - 2.5)
        b2y = cy + size * math.sin(angle - 2.5)
        ctx.rgb(g, g, g)
        ctx.move_to(ax, ay).line_to(b1x, b1y).line_to(b2x, b2y).fill()

    def _hint_line(self, ctx, text, y, left_angle, right_angle, g=0.6):
        try:
            w = ctx.text_width(text)
        except Exception:
            w = len(text) * HINT_FONT * 0.55
        ax = w / 2 + 12
        ctx.rgb(g, g, g)
        ctx.move_to(0, y).text(text)
        self._arrow(ctx, -ax, y, left_angle, 6, g)
        self._arrow(ctx, ax, y, right_angle, 6, g)

    def _draw_hints(self, ctx):
        ctx.font_size = HINT_FONT
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE
        self._hint_line(ctx, "switch effects (A/D)", -HINT_Y, DIR_UP, DIR_DOWN)

        ctx.rgb(0.6, 0.6, 0.6)
        ctx.move_to(0, HINT_Y - 9).text("change name colour")
        self._hint_line(ctx, "(C/E or shake)", HINT_Y + 9, DIR_E, DIR_C)


__app_export__ = StarNameApp

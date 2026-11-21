#import <Cocoa/Cocoa.h>

@interface EvaluatedItem : NSObject
@property (nonatomic, assign) NSInteger pid;
@property (nonatomic, copy) NSString *title;
@property (nonatomic, assign) double current;
@property (nonatomic, assign) double target;
@property (nonatomic, assign) double distPct;
@end

@implementation EvaluatedItem
@end

@interface AppDelegate : NSObject <NSApplicationDelegate>
@property (nonatomic, strong) NSStatusItem *statusItem;
@property (nonatomic, strong) NSTimer *timer;
@property (nonatomic, copy) NSString *baseUrl;
@property (nonatomic, copy) NSString *apiToken;
@property (nonatomic, copy) NSString *lastUpdated;
@property (nonatomic, copy) NSString *lastError;
@property (nonatomic, strong) NSArray<EvaluatedItem *> *greenItems;
@property (nonatomic, strong) NSArray<EvaluatedItem *> *yellowItems;
@property (nonatomic, strong) NSArray<EvaluatedItem *> *redItems;
@end

@implementation AppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    self.baseUrl = @"http://127.0.0.1:8080";
    self.apiToken = @"";
    self.lastUpdated = @"Never";
    self.greenItems = @[];
    self.yellowItems = @[];
    self.redItems = @[];

    [self loadEnv];

    self.statusItem = [[NSStatusBar systemStatusBar] statusItemWithLength:NSVariableStatusItemLength];
    self.statusItem.button.title = @"PriceBuddy ...";
    self.statusItem.button.font = [NSFont monospacedDigitSystemFontOfSize:12 weight:NSFontWeightMedium];

    [self rebuildMenu];
    [self fetchProducts];

    self.timer = [NSTimer scheduledTimerWithTimeInterval:60.0
                                                  target:self
                                                selector:@selector(fetchProducts)
                                                userInfo:nil
                                                 repeats:YES];
}

- (void)loadEnv {
    NSMutableArray<NSString *> *candidates = [NSMutableArray array];
    NSString *envOverride = NSProcessInfo.processInfo.environment[@"HARNESS_ENV_PATH"];
    if (envOverride.length > 0) [candidates addObject:[envOverride stringByExpandingTildeInPath]];
    [candidates addObject:[[NSHomeDirectory() stringByAppendingPathComponent:@"blackfriday-monitor/harness.env"] stringByStandardizingPath]];
    [candidates addObject:[[NSHomeDirectory() stringByAppendingPathComponent:@"agentic-ai-blackfriday-monitor/harness.env"] stringByStandardizingPath]];
    [candidates addObject:[[NSHomeDirectory() stringByAppendingPathComponent:@".config/agentic-blackfriday-monitor/harness.env"] stringByStandardizingPath]];

    NSString *content = nil;
    for (NSString *path in candidates) {
        content = [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:nil];
        if (content) break;
    }
    if (!content) return;

    for (NSString *raw in [content componentsSeparatedByCharactersInSet:[NSCharacterSet newlineCharacterSet]]) {
        NSString *line = [raw stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        if (line.length == 0 || [line hasPrefix:@"#"]) continue;
        NSRange eq = [line rangeOfString:@"="];
        if (eq.location == NSNotFound) continue;
        NSString *k = [[line substringToIndex:eq.location] stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
        NSString *v = [[line substringFromIndex:eq.location + 1] stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
        if ([k isEqualToString:@"PRICEBUDDY_URL"] && v.length > 0) {
            self.baseUrl = [v stringByTrimmingCharactersInSet:[NSCharacterSet characterSetWithCharactersInString:@"/"]];
        } else if ([k isEqualToString:@"PRICEBUDDY_API_TOKEN"] && v.length > 0) {
            self.apiToken = v;
        }
    }
}

- (void)fetchProducts {
    NSString *urlStr = [NSString stringWithFormat:@"%@/api/products?per_page=100", self.baseUrl];
    NSURL *url = [NSURL URLWithString:urlStr];
    if (!url) return;

    NSMutableURLRequest *req = [NSMutableURLRequest requestWithURL:url];
    req.timeoutInterval = 12.0;
    [req setValue:@"application/json" forHTTPHeaderField:@"Accept"];
    if (self.apiToken.length > 0) {
        [req setValue:[NSString stringWithFormat:@"Bearer %@", self.apiToken] forHTTPHeaderField:@"Authorization"];
    }

    __weak typeof(self) weakSelf = self;
    [[[NSURLSession sharedSession] dataTaskWithRequest:req completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        dispatch_async(dispatch_get_main_queue(), ^{
            __strong typeof(weakSelf) strongSelf = weakSelf;
            if (!strongSelf) return;

            if (error) {
                strongSelf.lastError = error.localizedDescription;
                strongSelf.statusItem.button.title = @"PB offline";
                [strongSelf rebuildMenu];
                return;
            }
            NSHTTPURLResponse *http = (NSHTTPURLResponse *)response;
            if (![http isKindOfClass:[NSHTTPURLResponse class]] || http.statusCode != 200 || !data) {
                strongSelf.lastError = @"Invalid HTTP status";
                strongSelf.statusItem.button.title = @"PB error";
                [strongSelf rebuildMenu];
                return;
            }

            NSError *jsonErr = nil;
            NSDictionary *root = [NSJSONSerialization JSONObjectWithData:data options:0 error:&jsonErr];
            NSArray *rows = [root isKindOfClass:[NSDictionary class]] ? root[@"data"] : nil;
            if (jsonErr || ![rows isKindOfClass:[NSArray class]]) {
                strongSelf.lastError = @"JSON parse error";
                strongSelf.statusItem.button.title = @"PB error";
                [strongSelf rebuildMenu];
                return;
            }

            NSMutableArray<EvaluatedItem *> *g = [NSMutableArray array];
            NSMutableArray<EvaluatedItem *> *y = [NSMutableArray array];
            NSMutableArray<EvaluatedItem *> *r = [NSMutableArray array];

            for (NSDictionary *p in rows) {
                if (![p isKindOfClass:[NSDictionary class]]) continue;
                id pausedVal = p[@"paused"];
                if ([pausedVal respondsToSelector:@selector(boolValue)] && [pausedVal boolValue]) continue;
                id curVal = p[@"current_price"];
                id tgtVal = p[@"notify_price"];
                if (![curVal respondsToSelector:@selector(doubleValue)] || ![tgtVal respondsToSelector:@selector(doubleValue)]) continue;
                double cur = [curVal doubleValue];
                double tgt = [tgtVal doubleValue];
                if (cur <= 0 || tgt <= 0) continue;

                EvaluatedItem *it = [[EvaluatedItem alloc] init];
                it.pid = [p[@"id"] integerValue];
                it.title = [p[@"title"] isKindOfClass:[NSString class]] ? p[@"title"] : @"Product";
                it.current = cur;
                it.target = tgt;
                it.distPct = ((cur - tgt) / tgt) * 100.0;

                if (it.distPct <= 0.0) {
                    [g addObject:it];
                } else if (it.distPct <= 15.0) {
                    [y addObject:it];
                } else {
                    [r addObject:it];
                }
            }

            NSSortDescriptor *sort = [NSSortDescriptor sortDescriptorWithKey:@"distPct" ascending:YES];
            strongSelf.greenItems = [g sortedArrayUsingDescriptors:@[sort]];
            strongSelf.yellowItems = [y sortedArrayUsingDescriptors:@[sort]];
            strongSelf.redItems = [r sortedArrayUsingDescriptors:@[sort]];
            strongSelf.lastError = nil;

            NSDateFormatter *df = [[NSDateFormatter alloc] init];
            df.dateFormat = @"HH:mm:ss";
            strongSelf.lastUpdated = [df stringFromDate:[NSDate date]];

            strongSelf.statusItem.button.title = [NSString stringWithFormat:@"🟢%lu 🟡%lu 🔴%lu",
                                                  (unsigned long)strongSelf.greenItems.count,
                                                  (unsigned long)strongSelf.yellowItems.count,
                                                  (unsigned long)strongSelf.redItems.count];
            [strongSelf rebuildMenu];
        });
    }] resume];
}

- (NSString *)formatPrice:(double)v {
    NSNumberFormatter *nf = [[NSNumberFormatter alloc] init];
    nf.numberStyle = NSNumberFormatterDecimalStyle;
    nf.minimumFractionDigits = 2;
    nf.maximumFractionDigits = 2;
    return [nf stringFromNumber:@(v)] ?: [NSString stringWithFormat:@"%.2f", v];
}

- (NSString *)shortTitle:(NSString *)s {
    NSString *cleaned = [s stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (cleaned.length <= 34) return cleaned;
    return [[cleaned substringToIndex:33] stringByAppendingString:@"…"];
}

- (NSMenuItem *)makeProductItemWithIcon:(NSString *)icon item:(EvaluatedItem *)item {
    NSString *sign = item.distPct > 0 ? @"+" : @"";
    NSString *pctStr = [NSString stringWithFormat:@"%@%.1f%%", sign, item.distPct];
    NSString *label = [NSString stringWithFormat:@"%@ %@ · %@ · %@", icon, [self shortTitle:item.title], [self formatPrice:item.current], pctStr];
    NSMenuItem *mi = [[NSMenuItem alloc] initWithTitle:label action:@selector(openProduct:) keyEquivalent:@""];
    mi.target = self;
    mi.tag = item.pid;
    mi.toolTip = [NSString stringWithFormat:@"%@\nCurrent: %@ | Target: %@", item.title, [self formatPrice:item.current], [self formatPrice:item.target]];
    return mi;
}

- (void)rebuildMenu {
    NSMenu *menu = [[NSMenu alloc] init];
    NSUInteger total = self.greenItems.count + self.yellowItems.count + self.redItems.count;

    NSMenuItem *header = [[NSMenuItem alloc] initWithTitle:[NSString stringWithFormat:@"Black Friday Monitor · %lu active", (unsigned long)total] action:nil keyEquivalent:@""];
    header.enabled = NO;
    [menu addItem:header];

    NSString *subText = self.lastError ? [NSString stringWithFormat:@"Error: %@", self.lastError] : [NSString stringWithFormat:@"Updated at %@", self.lastUpdated];
    NSMenuItem *subHeader = [[NSMenuItem alloc] initWithTitle:subText action:nil keyEquivalent:@""];
    subHeader.enabled = NO;
    [menu addItem:subHeader];
    [menu addItem:[NSMenuItem separatorItem]];

    if (self.greenItems.count > 0) {
        NSMenuItem *h = [[NSMenuItem alloc] initWithTitle:@"At target price" action:nil keyEquivalent:@""];
        h.enabled = NO;
        [menu addItem:h];
        for (EvaluatedItem *it in self.greenItems) {
            [menu addItem:[self makeProductItemWithIcon:@"🟢" item:it]];
        }
        [menu addItem:[NSMenuItem separatorItem]];
    }

    if (self.yellowItems.count > 0) {
        NSMenuItem *h = [[NSMenuItem alloc] initWithTitle:@"Near target up to +15%" action:nil keyEquivalent:@""];
        h.enabled = NO;
        [menu addItem:h];
        for (EvaluatedItem *it in self.yellowItems) {
            [menu addItem:[self makeProductItemWithIcon:@"🟡" item:it]];
        }
        [menu addItem:[NSMenuItem separatorItem]];
    }

    if (self.redItems.count > 0) {
        NSMenu *redMenu = [[NSMenu alloc] init];
        for (EvaluatedItem *it in self.redItems) {
            [redMenu addItem:[self makeProductItemWithIcon:@"🔴" item:it]];
        }
        NSMenuItem *redRoot = [[NSMenuItem alloc] initWithTitle:[NSString stringWithFormat:@"🔴 Above +15%% · %lu items", (unsigned long)self.redItems.count] action:nil keyEquivalent:@""];
        redRoot.submenu = redMenu;
        [menu addItem:redRoot];
        [menu addItem:[NSMenuItem separatorItem]];
    }

    NSMenuItem *openApp = [[NSMenuItem alloc] initWithTitle:@"Open PriceBuddy Window" action:@selector(openDashboard) keyEquivalent:@"o"];
    openApp.target = self;
    [menu addItem:openApp];

    NSMenuItem *refreshNow = [[NSMenuItem alloc] initWithTitle:@"Refresh Now" action:@selector(fetchProducts) keyEquivalent:@"r"];
    refreshNow.target = self;
    [menu addItem:refreshNow];

    [menu addItem:[NSMenuItem separatorItem]];
    NSMenuItem *quitItem = [[NSMenuItem alloc] initWithTitle:@"Quit" action:@selector(quitApp) keyEquivalent:@"q"];
    quitItem.target = self;
    [menu addItem:quitItem];

    self.statusItem.menu = menu;
}

- (void)openDashboard {
    NSURL *appUrl = [NSURL fileURLWithPath:@"/Applications/PriceBuddy.app"];
    if ([[NSFileManager defaultManager] fileExistsAtPath:appUrl.path]) {
        [[NSWorkspace sharedWorkspace] openApplicationAtURL:appUrl configuration:[NSWorkspaceOpenConfiguration configuration] completionHandler:nil];
    } else {
        NSURL *url = [NSURL URLWithString:[NSString stringWithFormat:@"%@/admin", self.baseUrl]];
        if (url) [[NSWorkspace sharedWorkspace] openURL:url];
    }
}

- (void)openProduct:(NSMenuItem *)sender {
    NSInteger pid = sender.tag;
    NSString *urlStr = [NSString stringWithFormat:@"%@/admin/products/%ld", self.baseUrl, (long)pid];
    NSString *chrome = @"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
    if ([[NSFileManager defaultManager] fileExistsAtPath:chrome]) {
        NSTask *task = [[NSTask alloc] init];
        task.executableURL = [NSURL fileURLWithPath:chrome];
        task.arguments = @[[NSString stringWithFormat:@"--app=%@", urlStr], @"--profile-directory=Default"];
        [task launchAndReturnError:nil];
    } else {
        NSURL *url = [NSURL URLWithString:urlStr];
        if (url) [[NSWorkspace sharedWorkspace] openURL:url];
    }
}

- (void)quitApp {
    [NSApp terminate:nil];
}

@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *app = [NSApplication sharedApplication];
        [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
        AppDelegate *delegate = [[AppDelegate alloc] init];
        app.delegate = delegate;
        [app run];
    }
    return 0;
}

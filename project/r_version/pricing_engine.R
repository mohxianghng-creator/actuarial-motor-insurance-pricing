# Native R scoring and monetary pricing. No Python or nonstandard package required.
read_parameters <- function(directory) {
  p <- read.csv(file.path(directory,"parameters.csv"),stringsAsFactors=FALSE,check.names=FALSE)
  out <- as.list(p$value); names(out) <- p$parameter
  numeric_names <- setdiff(names(out),c("input_sha256","source_candidate","version","margin_mode"))
  for(n in numeric_names) out[[n]] <- as.numeric(out[[n]])
  out$tier_risk_margins <- unlist(out[paste0("tier_money_",1:3)],use.names=FALSE)
  out$enforce_cost_floor <- isTRUE(out$enforce_cost_floor==1)
  out
}

read_portable_model <- function(directory) list(parameters=read_parameters(directory),
  terms=read.csv(file.path(directory,"terms.csv"),stringsAsFactors=FALSE,check.names=FALSE),
  reference=read.csv(file.path(directory,"reference_ranges.csv"),stringsAsFactors=FALSE))

validate_risks <- function(d) {
  features <- c("Power","CarAge","DriverAge","Brand","Gas","Region","Density")
  if(!all(c("PolicyID",features)%in%names(d))) stop("Missing risk columns")
  if(anyNA(d$PolicyID)||anyDuplicated(d$PolicyID)) stop("PolicyID must be unique and complete")
  for(n in c("CarAge","DriverAge","Density")) {
    old <- d[[n]]; value <- suppressWarnings(as.numeric(old))
    if(any(is.na(value)&!is.na(old)&as.character(old)!="")) stop(paste("Nonnumeric",n))
    if(any(!is.finite(value[!is.na(value)]))||any(value<0,na.rm=TRUE)) stop(paste("Invalid",n))
    d[[n]] <- value
  }
  d
}

predict_portable <- function(model,d) {
  d <- validate_risks(d);eta <- rep(model$parameters$intercept,nrow(d))
  for(i in seq_len(nrow(model$terms))) {
    t <- model$terms[i,]; v <- d[[t$feature]]
    if(t$encoding=="onehot") {v <- as.character(v);v[is.na(v)] <- "Missing";eta <- eta+t$coefficient*as.numeric(v==t$level)}
    else {v <- as.numeric(v);if(t$encoding=="log1p") v <- log1p(v);v[is.na(v)] <- t$imputation_transformed;eta <- eta+t$coefficient*(v-t$center)/t$scale}
  }
  frequency <- pmax(exp(eta),1e-9)
  data.frame(PolicyID=d$PolicyID,frequency=frequency,severity=model$parameters$pooled_severity,
    pure_premium=frequency*model$parameters$pooled_severity*model$parameters$calibration_factor)
}

money_margin <- function(pure,p,thresholds) {
  values <- p$tier_risk_margins
  if(length(values)!=length(thresholds)+1||any(!is.finite(values))||any(values<0)||any(diff(values)<0)||any(!is.finite(thresholds))||any(diff(thresholds)<=0))stop("Invalid locked money tiers")
  if(p$margin_mode=="flat_constant") {if(!is.finite(p$flat_risk_margin)||p$flat_risk_margin<0)stop("Invalid flat margin");return(rep(p$flat_risk_margin,length(pure)))}
  if(p$margin_mode=="tiered_constant")return(values[findInterval(pure,thresholds)+1])
  if(p$margin_mode=="smooth_tiered_constant") {
    if(!is.finite(p$transition_width)||p$transition_width<=0)stop("Invalid shoulder width")
    result <- rep(values[1],length(pure))
    for(k in seq_along(thresholds)) {u <- pmin(1,pmax(0,(pure-thresholds[k]+p$transition_width/2)/p$transition_width));result <- result+(values[k+1]-values[k])*(3*u^2-2*u^3)}
    return(result)
  }
  stop("Unknown money rule")
}

quote_prices <- function(pure,p,thresholds) {
  vals <- unlist(p[c("trend_factor","development_factor","fixed_expense","variable_expense_ratio","tax_ratio","commercial_adjustment")])
  if(any(!is.finite(pure))||any(pure<0)||any(!is.finite(vals)))stop("Invalid monetary inputs")
  v <- p$variable_expense_ratio;t <- p$tax_ratio
  if(v<0||t<0||v+t>=1||p$fixed_expense<0||p$trend_factor<=0||p$development_factor<=0)stop("Invalid financial assumptions")
  loss <- pure*p$trend_factor*p$development_factor;money <- money_margin(pure,p,thresholds)
  floor <- (loss+p$fixed_expense)/(1-v-t);technical <- (loss+p$fixed_expense+money)/(1-v-t)
  requested <- technical+p$commercial_adjustment;quoted <- if(p$enforce_cost_floor)pmax(requested,floor) else requested
  if(any(quoted<0))stop("Negative quote")
  data.frame(pure_premium=pure,expected_loss=loss,money_margin=money,fixed_expense=p$fixed_expense,cost_floor=floor,
    technical_premium=technical,quote=quoted,applied_adjustment=quoted-technical,floor_applied=requested<floor,
    expected_contribution=quoted*(1-v-t)-loss-p$fixed_expense)
}

quote_portable <- function(model,d,pricing=model$parameters) {
  d <- validate_risks(d);pred <- predict_portable(model,d);thresholds <- unlist(model$parameters[c("threshold_1","threshold_2")],use.names=FALSE)
  out <- cbind(PolicyID=d$PolicyID,quote_prices(pred$pure_premium,pricing,thresholds));out$frequency <- pred$frequency
  flags <- rep("",nrow(d))
  add <- function(condition,label) {flags[condition] <<- ifelse(flags[condition]=="",label,paste(flags[condition],label,sep="; "))}
  for(feature in unique(model$reference$feature)) {
    r <- model$reference[model$reference$feature==feature,];v <- d[[feature]]
    add(is.na(v),paste("Missing",feature))
    if(r$kind[1]=="numeric")add(!is.na(v)&(v<as.numeric(r$minimum[1])|v>as.numeric(r$maximum[1])),paste("Out-of-range",feature))
    else add(!is.na(v)&!(as.character(v)%in%r$level),paste("Unseen",feature))
  }
  add(!is.na(d$CarAge)&d$CarAge>40,"Vehicle age over 40")
  out$review_reasons <- flags;out$manual_review <- flags!="";out$usage_status <- "RESEARCH_ONLY_NOT_APPROVED_TARIFF"
  out
}

calibrate_level <- function(y,p,mode="credibility",prior_sd=.2) {
  if(length(y)!=length(p)||any(!is.finite(y))||any(!is.finite(p))||any(y<0)||any(p<=0)||sum(y)<=0||!is.finite(prior_sd)||prior_sd<=0)stop("Invalid calibration inputs")
  ratio <- sum(y)/sum(p);variance <- sum((y-p)^2)/sum(y)^2;z <- prior_sd^2/(prior_sd^2+variance)
  factor <- switch(mode,none=1,full=ratio,credibility=exp(z*log(ratio)),stop("Unknown calibration mode"))
  list(factor=factor,raw_ratio=ratio,reliability=z,log_ratio_variance_approximation=variance,mode=mode)
}

market_scenario <- function(prices,reference_price,reference_risk,c,competitor_multiplier=1,risk_tilt=0,elasticity=5,bound_loss_multiplier=1,adjustment=0) {
  if(competitor_multiplier<=0||elasticity<=0||bound_loss_multiplier<=0||c$reference_conversion<=0||c$reference_conversion>=1||c$expected_capacity<0)stop("Invalid market assumptions")
  competitor <- reference_price*competitor_multiplier*exp(risk_tilt*log(reference_risk/median(reference_risk)))
  offered <- pmax(prices$technical_premium+adjustment,prices$cost_floor)
  demand <- plogis(qlogis(c$reference_conversion)-elasticity*log(offered/competitor))
  acceptance <- min(1,c$expected_capacity/max(sum(demand),1e-12));bound <- demand*acceptance
  loss <- prices$expected_loss*bound_loss_multiplier
  premium <- sum(offered*bound);expenses <- sum((offered*(1-c$net_premium_ratio)+prices$fixed_expense)*bound)
  data.frame(expected_policies=sum(bound),unconstrained_expected_policies=sum(demand),random_acceptance_ratio=acceptance,
    expected_premium=premium,expected_loss=sum(loss*bound),expected_expenses=expenses,
    expected_contribution=sum((offered*c$net_premium_ratio-loss-prices$fixed_expense)*bound),
    combined_ratio=(sum(loss*bound)+expenses)/max(premium,1e-12),growth_target_met=sum(bound)>=c$minimum_expected_policies,
    competitor_multiplier=competitor_multiplier,risk_tilt=risk_tilt,elasticity=elasticity,bound_loss_multiplier=bound_loss_multiplier,money_adjustment=adjustment)
}

commercial_grid <- function(prices,c) {
  rows <- list();i <- 0
  for(comp in c$competitor_multipliers)for(tilt in c$competitor_risk_tilts)for(beta in c$elasticities)for(bound in c$bound_loss_multipliers)for(adj in c$quote_adjustments) {
    i <- i+1;rows[[i]] <- market_scenario(prices,prices$technical_premium,prices$pure_premium,c,comp,tilt,beta,bound,adj)
  }
  grid <- do.call(rbind,rows)
  robust <- do.call(rbind,lapply(split(grid,grid$money_adjustment),function(g)data.frame(money_adjustment=g$money_adjustment[1],worst_contribution=min(g$expected_contribution),best_contribution=max(g$expected_contribution),minimum_expected_policies=min(g$expected_policies),all_growth_targets_met=all(g$growth_target_met))))
  robust <- robust[order(robust$money_adjustment),];rownames(robust) <- NULL
  robust$profit_floor_met <- robust$worst_contribution>=c$minimum_worst_expected_contribution
  robust$joint_constraints_met <- robust$all_growth_targets_met&robust$profit_floor_met
  list(grid=grid,robust=robust,feasible=any(robust$joint_constraints_met),selected_adjustment=if(any(robust$joint_constraints_met))robust$money_adjustment[which.max(ifelse(robust$joint_constraints_met,robust$worst_contribution,-Inf))] else NA_real_)
}

# Base-R diagnostics and reporting. R random streams are not NumPy random streams.
run_diagnostics <- function(config,model,prices,commercial) {
  set.seed(config$seed+333);all<-read.csv("data/training_and_calibration.csv",stringsAsFactors=FALSE);dev<-all[all$split%in%c("train","validation"),]
  y<-dev$ClaimAmount;pos<-y[y>0];n<-nrow(prices);target<-sum(prices$expected_loss);p0<-length(pos)/length(y);scale<-target/(n*mean(y));rc<-config$risk_budget
  cases<-expand.grid(cv=rc$frequency_systematic_cvs,severity=rc$severity_factors);cases$event<-FALSE;cases<-rbind(cases,data.frame(cv=0,severity=1,event=TRUE))
  budgets<-list();index<-0
  for(i in seq_len(nrow(cases))) {
    ca<-cases[i,];frailty<-if(ca$cv==0)rep(1,rc$simulations) else rgamma(rc$simulations,shape=1/ca$cv^2,scale=ca$cv^2)
    counts<-rbinom(rc$simulations,n,pmin(1,p0*frailty));sim<-vapply(counts,function(k)sum(sample(pos,k,replace=TRUE))*scale*ca$severity,numeric(1))
    event_loss<-n*rc$common_event_affected_share*rc$common_event_loss_per_affected_policy;prob<-if(ca$event)rc$common_event_probability else 0
    if(ca$event)sim<-sim+rbinom(rc$simulations,1,prob)*event_loss
    expected<-target*ca$severity+prob*event_loss
    for(q in rc$tail_quantiles){v<-as.numeric(quantile(sim,q,type=7));capital<-max(0,v-expected);index<-index+1;budgets[[index]]<-data.frame(frequency_systematic_cv=ca$cv,severity_factor=ca$severity,event_probability=prob,quantile=q,expected_loss=expected,simulated_mean=mean(sim),aggregate_loss_quantile=v,tail_mean=mean(sim[sim>=v]),buffer_money_per_policy=capital/n,CoC_money_margin_per_policy=capital/n*rc$cost_of_capital,additional_expected_event_loss_per_policy=prob*event_loss/n)}
  }
  budget<-do.call(rbind,budgets);write.csv(budget,"results/r_risk_budget_scenarios.csv",row.names=FALSE)
  tail<-list()
  for(q in c(.95,.975,.99)) {
    u<-as.numeric(quantile(pos,q,type=7));excess<-pos[pos>u]-u
    nll<-function(t){shape<-t[1];scale<-exp(t[2]);v<-1+shape*excess/scale;if(any(v<=0))return(1e100);if(abs(shape)<1e-7)length(excess)*log(scale)+sum(excess)/scale else length(excess)*log(scale)+(1+1/shape)*sum(log(v))}
    fit<-optim(c(.5,log(mean(excess))),nll,method="L-BFGS-B",lower=c(-.45,-10),upper=c(2,30))
    tail[[as.character(q)]]<-data.frame(positive_policy_quantile=q,threshold=u,exceedances=length(excess),GPD_shape=fit$par[1],GPD_scale=exp(fit$par[2]),finite_variance_fit=fit$par[1]<.5,optimizer_convergence=fit$convergence)
  }
  write.csv(do.call(rbind,tail),"results/r_tail_threshold_sensitivity.csv",row.names=FALSE)
  counts<-dev$ClaimNb;mu<-mean(counts)
  nb<-optim(log(c(mu,.5)),function(t)-sum(dnbinom(counts,size=1/exp(t[2]),mu=exp(t[1]),log=TRUE)),method="L-BFGS-B",lower=c(-12,-12),upper=c(3,8))
  zip<-optim(c(-3,log(mu*1.1)),function(t){pi<-plogis(t[1]);lambda<-exp(t[2]);-sum(ifelse(counts==0,log(pi+(1-pi)*exp(-lambda)),log1p(-pi)+dpois(counts,lambda,log=TRUE)))},method="L-BFGS-B",lower=c(-18,-12),upper=c(10,3))
  zeros<-data.frame(observed_zero_fraction=mean(counts==0),poisson_expected_zero=exp(-mu),variance_to_mean=mean((counts-mu)^2)/mu,NB_alpha=exp(nb$par[2]),ZIP_structural_parameter_intercept_only=plogis(zip$par[1]))
  write.csv(zeros,"results/r_zero_count_diagnostics.csv",row.names=FALSE)
  oof<-read.csv("portable_model/python_development_oof_predictions.csv",stringsAsFactors=FALSE)
  joined<-dev[match(oof$PolicyID,dev$PolicyID),];joined$candidate_prediction<-oof$candidate_prediction
  joined$DriverAgeBand<-cut(joined$DriverAge,c(0,25,35,50,65,120),right=FALSE)
  ranked<-rank(joined$candidate_prediction,ties.method="first")
  joined$RiskDecile<-as.character(cut(ranked,breaks=quantile(ranked,seq(0,1,length.out=11),type=7),include.lowest=TRUE,labels=FALSE))
  groups<-list();index<-0;set.seed(config$seed)
  for(feature in c("DriverAgeBand","Region","Gas","Brand","RiskDecile"))for(level in unique(as.character(joined[[feature]]))) {
    g<-joined[as.character(joined[[feature]])==level,];loss<-g$ClaimAmount;p<-g$candidate_prediction;total<-sum(loss)
    intervals<-replicate(400,{ix<-sample.int(nrow(g),nrow(g),replace=TRUE);sum(loss[ix])/sum(p[ix])})
    effective<-total^2/sum(loss^2);largest<-max(loss)/total;claims<-sum(g$ClaimNb)
    index<-index+1;groups[[index]]<-data.frame(feature=feature,level=level,policies=nrow(g),claims=claims,effective_loss_count=effective,largest_loss_share=largest,actual_over_expected=total/sum(p),AE_lower=quantile(intervals,.025),AE_upper=quantile(intervals,.975),low_credibility=claims<config$monitoring$minimum_segment_claims||effective<config$monitoring$minimum_effective_loss_count||largest>config$monitoring$maximum_top_loss_share)
  }
  groups<-do.call(rbind,groups);write.csv(groups,"results/r_segment_credibility_canonical_oof.csv",row.names=FALSE)
  margins<-list();thresholds<-unlist(model$parameters[c("threshold_1","threshold_2")],use.names=FALSE)
  for(mode in c("flat_constant","tiered_constant","smooth_tiered_constant")){p<-model$parameters;p$margin_mode<-mode;v<-quote_prices(prices$pure_premium,p,thresholds);margins[[mode]]<-data.frame(mode=mode,mean_quote=mean(v$quote),mean_money=mean(v$money_margin),total_contribution=sum(v$expected_contribution))}
  write.csv(do.call(rbind,margins),"results/r_margin_comparison.csv",row.names=FALSE)
  html_escape<-function(x){x<-gsub('&','&amp;',as.character(x),fixed=TRUE);x<-gsub('<','&lt;',x,fixed=TRUE);gsub('>','&gt;',x,fixed=TRUE)}
  table_html<-function(d)paste0('<table><tr>',paste0('<th>',html_escape(names(d)),'</th>',collapse=''),'</tr>',paste(apply(d,1,function(row)paste0('<tr>',paste0('<td>',html_escape(row),'</td>',collapse=''),'</tr>')),collapse=''),'</table>')
  nativefile<-"results/native_r_cv_model_summary.csv"
  native<-if(file.exists(nativefile))read.csv(nativefile,stringsAsFactors=FALSE) else NULL
  report<-paste0('<!doctype html><html lang="en"><meta charset="utf-8"><title>Native R Motor Pricing V2</title><style>body{font:15px/1.6 Arial;color:#173549;max-width:1100px;margin:35px auto;padding:20px}table{border-collapse:collapse;font-size:11px;width:100%}td,th{padding:7px;border-bottom:1px solid #d9e4e8;text-align:left}th{background:#e4f2ef}h1,h2{color:#087f82}</style><h1>Motor Pricing V2 - R Version</h1><p>Research only. Exact port of the frozen Python-selected model and separately labeled native R refitting.</p><h2>Exact quote and business-rule portability</h2><p>Policies: ',nrow(prices),'. Mean quote: ',format(mean(prices$quote),digits=10),'. Joint commercial constraints feasible: ',commercial$feasible,'. No independent test or market-profit uplift is claimed.</p>',table_html(read.csv('results/python_r_scoring_parity.csv')),'<h2>Native R research comparison</h2><p>GLM/GAM share declared log-link/penalty/spline objectives. GBM uses native rpart Newton trees, not sklearn histogram boosting. Its refit results can differ. The canonical imported model is not replaced automatically.</p>',if(!is.null(native))table_html(head(native,12)) else '<p>Run run.R --research for native development comparisons.</p>','<h2>Commercial robustness</h2>',table_html(commercial$robust),'<h2>Conditional risk budgets</h2><p>Independent policy bootstrap and assumed systematic/event scenarios. R random streams differ from NumPy; numerical simulations are not claimed bit-identical. Capital buffer, capital cost and expected additional event loss are distinct. No reserve/reinsurance or regulatory-capital calculation.</p>',table_html(budget[budget$quantile==.99,]),'<h2>Zero and tail diagnostics</h2><p>Many zeros do not identify structural immunity. Tail fits apply to aggregate policy loss and are threshold sensitive; they do not enter prices automatically.</p>',table_html(zeros),table_html(do.call(rbind,tail)),'<h2>Credibility and evidence provenance</h2><p>R recomputes aggregates and conditional bootstrap intervals from the same canonical Python repeated-OOF scores, explicitly imported. This is not native-R OOF validation and not fresh test evidence. Native R model comparison is separately available.</p>',table_html(head(groups,10)),'<h2>Governance</h2><p>Exposure, loss valuation, currency, contract terms, actual expenses, reinsurance, market demand and jurisdiction remain unconfirmed. Original V2 professional/regulatory reasoning and approval gates apply. Software parity does not certify compliance or tariff adequacy.</p></html>')
  writeLines(report,'results/R_ACTUARIAL_REPORT.html',useBytes=TRUE)
  pdf('results/R_EVIDENCE_REPORT.pdf',width=11.7,height=8.3)
  par(mfrow=c(2,2),mar=c(5,5,4,2));barplot(unlist(lapply(margins,function(g)g$mean_quote)),names.arg=c('Flat','Strict tiers','Smooth'),col='#087f82',main='Frozen model: mean quote by money rule',ylab='Unspecified monetary units')
  plot(commercial$robust$money_adjustment,commercial$robust$worst_contribution,type='b',col='#b97322',main='Worst conditional contribution',xlab='Money adjustment',ylab='Contribution');abline(h=0,lty=2)
  points<-seq(thresholds[1]-30,thresholds[2]+35,length.out=200);plot(points,points,type='n',ylim=c(50,250),main='Money-rule boundary behavior',xlab='Pure loss',ylab='Quote')
  for(i in seq_along(margins)){p<-model$parameters;p$margin_mode<-names(margins)[i];lines(points,quote_prices(points,p,thresholds)$quote,col=c('#376baf','#b97322','#087f82')[i],lwd=2)}
  legend('topleft',c('Flat','Strict tiers','Smooth shoulders'),col=c('#376baf','#b97322','#087f82'),lty=1,bty='n')
  ordinary<-budget[budget$quantile==.99&budget$event_probability==0,];barplot(ordinary$CoC_money_margin_per_policy,col='#607785',main='Conditional 99% capital-cost charge',ylab='Money per policy',names.arg=paste0('CV=',ordinary$frequency_systematic_cv,'; sev=',ordinary$severity_factor),cex.names=.65)
  dev.off()
  invisible(list(budget=budget,segments=groups))
}
